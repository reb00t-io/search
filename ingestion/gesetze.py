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
from ingestion.chunking import MAX_CHUNK_WORDS, chunk_text

logger = logging.getLogger(__name__)

# §-aligned chunking: one chunk per section; consecutive tiny sections are
# merged so chunks stay above the filter's minimum length, and a section is
# only ever split when it alone exceeds MAX_CHUNK_WORDS. Small targets keep
# retrieval §-precise ("§ 23 KStG" should hit the chunk containing § 23).
SECTION_TARGET_WORDS = 300
SECTION_MIN_WORDS = 40

TOC_URL = "https://www.gesetze-im-internet.de/gii-toc.xml"

# High-value statutes for the German tax/law research assistant, ingested
# before the rest of the (alphabetical) TOC. Matched against the URL slug
# (e.g. "estg", "ao_1977" — slugs may carry a year suffix).
PRIORITY_LAWS = [
    "hgb", "gmbhg", "aktg", "umwg", "ao", "estg", "kstg", "gewstg", "ustg",
    "grestg", "erbstg", "bewg", "fgo", "stberg", "bgb", "zpo", "inso", "bdsg",
]

_SLUG_RE = re.compile(r"gesetze-im-internet\.de/([^/]+)/")


def _toc_priority(link: str) -> int:
    """Rank a TOC entry: index into PRIORITY_LAWS, or len(PRIORITY_LAWS) if unlisted."""
    match = _SLUG_RE.search(link)
    if match:
        slug = match.group(1).lower()
        for i, abbrev in enumerate(PRIORITY_LAWS):
            if slug == abbrev or slug.startswith(abbrev + "_"):
                return i
    return len(PRIORITY_LAWS)


def sort_toc(entries: list[dict]) -> list[dict]:
    """Move priority statutes to the front, keeping TOC order otherwise."""
    return sorted(entries, key=lambda e: _toc_priority(e["link"]))


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


def _section_header(section: dict) -> str:
    if section["section_num"]:
        header = f"## {section['section_num']}"
        if section["section_title"]:
            header += f" {section['section_title']}"
        return header
    if section["section_title"]:
        return f"## {section['section_title']}"
    return ""


def build_section_chunks(
    sections: list[dict],
    law_title: str,
    law_abbrev: str,
    target_words: int = SECTION_TARGET_WORDS,
    min_words: int = SECTION_MIN_WORDS,
) -> list[dict]:
    """Chunk a law along § boundaries.

    Returns [{"text": ..., "sections": ["§ 22", "§ 23", ...]}, ...] where
    - every chunk starts with a `# <law title> (<abbrev>)` line so BM25 and
      embeddings carry the law tokens in every chunk,
    - a § is never split across chunks unless it alone exceeds
      MAX_CHUNK_WORDS (then it is sub-split with the heading repeated),
    - consecutive tiny sections are merged up to target_words.
    """
    title_line = f"# {law_title} ({law_abbrev})" if law_abbrev else f"# {law_title}"

    # (text, section_num, standalone) pieces in document order
    pieces: list[tuple[str, str, bool]] = []
    for section in sections:
        header = _section_header(section)
        body = f"{header}\n\n{section['text']}" if header else section["text"]
        if len(body.split()) > MAX_CHUNK_WORDS:
            for i, part in enumerate(chunk_text(section["text"])):
                part_header = f"{header} (Teil {i + 1})" if header else ""
                text = f"{part_header}\n\n{part}" if part_header else part
                pieces.append((text, section["section_num"], True))
        else:
            pieces.append((body, section["section_num"], False))

    chunks: list[dict] = []
    current_texts: list[str] = []
    current_sections: list[str] = []

    def flush() -> None:
        nonlocal current_texts, current_sections
        if current_texts:
            chunks.append({
                "text": title_line + "\n\n" + "\n\n".join(current_texts),
                "sections": [s for s in current_sections if s],
            })
            current_texts, current_sections = [], []

    for text, section_num, standalone in pieces:
        if standalone:
            flush()
            current_texts, current_sections = [text], [section_num]
            flush()
            continue
        current_words = sum(len(t.split()) for t in current_texts)
        if (
            current_words >= min_words
            and current_words + len(text.split()) > target_words
        ):
            flush()
        current_texts.append(text)
        current_sections.append(section_num)
    flush()

    # A trailing mini-chunk would be dropped by the length filter — merge it
    # into the previous chunk instead of losing the law's last sections.
    if (
        len(chunks) >= 2
        and len(chunks[-1]["text"].split()) < min_words + len(title_line.split())
    ):
        tail = chunks.pop()
        body = tail["text"].removeprefix(title_line).strip()
        chunks[-1]["text"] += "\n\n" + body
        chunks[-1]["sections"].extend(tail["sections"])

    return chunks


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
        return sort_toc(entries)

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

            # §-aligned chunking (see build_section_chunks)
            chunks = build_section_chunks(sections, law_title, law_abbrev)

            for chunk_idx, chunk in enumerate(chunks):
                doc_id = f"gesetze:{slug}:{chunk_idx}"
                yield Document(
                    id=doc_id,
                    source="gesetze",
                    title=law_title,
                    url=f"https://www.gesetze-im-internet.de/{slug}/",
                    language="de",
                    text=chunk["text"],
                    metadata={
                        "law_abbrev": law_abbrev,
                        "chunk_index": chunk_idx,
                        "sections": chunk["sections"],
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
