"""gesetze-im-internet.de source adapter — German federal laws."""

from __future__ import annotations

import io
import logging
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator

import httpx

from ingestion.base import Document, SourceAdapter
from ingestion.chunking import chunk_text

logger = logging.getLogger(__name__)

TOC_URL = "https://www.gesetze-im-internet.de/gii-toc.xml"


def _xml_text(el: ET.Element | None) -> str:
    """Extract text content from an XML element, including tail text of children."""
    if el is None:
        return ""
    parts = []
    if el.text:
        parts.append(el.text)
    for child in el:
        parts.append(_xml_text(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _parse_law_xml(xml_text: str) -> list[dict]:
    """Parse a law XML file into sections with title, text, and section number."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning("Failed to parse law XML: %s", e)
        return []

    # Extract law metadata
    meta = root.find(".//metadaten")
    law_title = ""
    law_abbrev = ""
    if meta is not None:
        jurabk = meta.find("jurabk")
        langue = meta.find("langue")
        if jurabk is not None and jurabk.text:
            law_abbrev = jurabk.text.strip()
        if langue is not None and langue.text:
            law_title = langue.text.strip()
    if not law_title:
        law_title = law_abbrev or "Unbekanntes Gesetz"

    sections = []
    for norm in root.findall(".//norm"):
        norm_meta = norm.find("metadaten")
        if norm_meta is None:
            continue

        enbez = norm_meta.find("enbez")
        titel = norm_meta.find("titel")
        section_num = enbez.text.strip() if enbez is not None and enbez.text else ""
        section_title = titel.text.strip() if titel is not None and titel.text else ""

        # Extract text content from textdaten/text/Content
        textdaten = norm.find("textdaten")
        if textdaten is None:
            continue
        text_el = textdaten.find("text")
        if text_el is None:
            continue
        content = text_el.find("Content")
        if content is None:
            continue

        text = _xml_text(content).strip()
        # Clean up whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)

        if not text or len(text) < 20:
            continue

        sections.append({
            "section_num": section_num,
            "section_title": section_title,
            "text": text,
            "law_title": law_title,
            "law_abbrev": law_abbrev,
        })

    return sections


class GesetzeAdapter(SourceAdapter):
    """Fetches German federal laws from gesetze-im-internet.de."""

    name = "gesetze"

    def __init__(self):
        self.client = httpx.Client(
            timeout=30,
            follow_redirects=True,
            headers={
                "User-Agent": "SearchEngineBot/1.0 (research project; contact: search@reb00t.io)",
            },
        )

    def _fetch_toc(self) -> list[dict]:
        """Fetch the table of contents listing all laws."""
        resp = self.client.get(TOC_URL)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        entries = []
        for item in root.findall("item"):
            title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            if title and link:
                entries.append({"title": title, "link": link})
        return entries

    def _fetch_law_xml(self, zip_url: str) -> str | None:
        """Download a law's XML zip and extract the XML content."""
        try:
            # Ensure HTTPS
            zip_url = zip_url.replace("http://", "https://")
            resp = self.client.get(zip_url)
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                xml_files = [n for n in zf.namelist() if n.endswith(".xml")]
                if not xml_files:
                    return None
                return zf.read(xml_files[0]).decode("utf-8", errors="replace")
        except Exception as e:
            logger.warning("Failed to fetch %s: %s", zip_url, e)
            return None

    def bulk_ingest(self, limit: int | None = None, known_ids: set[str] | None = None) -> Iterator[Document]:
        """Fetch laws from gesetze-im-internet.de."""
        toc = self._fetch_toc()
        logger.info("Found %d laws in TOC", len(toc))

        if limit:
            toc = toc[:limit]

        doc_count = 0
        for entry in toc:
            xml_content = self._fetch_law_xml(entry["link"])
            if not xml_content:
                continue

            sections = _parse_law_xml(xml_content)
            if not sections:
                continue

            law_abbrev = sections[0]["law_abbrev"] if sections else ""
            law_title = sections[0]["law_title"] if sections else entry["title"]
            slug = re.sub(r"[^a-z0-9]+", "-", law_abbrev.lower()).strip("-") or "gesetz"

            # Assemble full law text with section headings, then chunk
            full_text = ""
            for section in sections:
                header = ""
                if section["section_num"]:
                    header = f"## {section['section_num']}"
                    if section["section_title"]:
                        header += f" {section['section_title']}"
                elif section["section_title"]:
                    header = f"## {section['section_title']}"

                if header:
                    full_text += header + "\n\n" + section["text"] + "\n\n"
                else:
                    full_text += section["text"] + "\n\n"

            title_with_abbrev = f"{law_title} ({law_abbrev})" if law_abbrev else law_title
            chunks = chunk_text(full_text, title=title_with_abbrev)

            for chunk_idx, chunk in enumerate(chunks):
                doc_id = f"gesetze:{slug}:{chunk_idx}"
                yield Document(
                    id=doc_id,
                    source="gesetze",
                    title=law_title,
                    url=f"https://www.gesetze-im-internet.de/{slug}/",
                    language="de",
                    text=chunk,
                    metadata={
                        "law_abbrev": law_abbrev,
                        "chunk_index": chunk_idx,
                    },
                    timestamp="",
                )
                doc_count += 1

            # Rate limit
            time.sleep(0.2)

        logger.info("Extracted %d document chunks from %d laws", doc_count, len(toc))

    def document_url(self, doc_id: str) -> str:
        slug = doc_id.split(":")[1]
        return f"https://www.gesetze-im-internet.de/{slug}/"
