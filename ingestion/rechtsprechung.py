"""rechtsprechung-im-internet.de source adapter — German federal court decisions.

Covers decisions of the federal courts (BVerfG, BGH, BVerwG, BFH, BAG, BSG,
BPatG, ...) published since 2010. Uses the same juris infrastructure as
gesetze-im-internet.de: a TOC XML listing one zip per decision, each zip
containing a structured XML document.

Court decisions and official headnotes are not protected by copyright
(§ 5 UrhG), so the content is freely reusable.
"""

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

TOC_URL = "https://www.rechtsprechung-im-internet.de/rii-toc.xml"

# Tax case law (BFH) is the highest-value court for the German tax/law
# assistant, so its decisions are ingested first.
PRIORITY_COURTS = ["BFH"]

# Content sections of a decision XML, in reading order, with markdown headings.
_SECTIONS = [
    ("leitsatz", "Leitsatz"),
    ("tenor", "Tenor"),
    ("tatbestand", "Tatbestand"),
    ("entscheidungsgruende", "Entscheidungsgründe"),
    ("gruende", "Gründe"),
]


def _block_text(el: ET.Element | None) -> str:
    """Extract readable text from juris HTML-ish content markup.

    Paragraph-level elements become paragraph breaks; whitespace inside a
    paragraph is collapsed. ``dt`` elements (margin numbers / anchors) are
    skipped.
    """
    if el is None:
        return ""

    block_tags = {"p", "dd", "div", "dl", "table", "tr", "li", "h1", "h2", "h3"}
    # Sentinel marking a paragraph break — raw newlines in the (pretty-printed)
    # XML are ordinary whitespace and must not split paragraphs.
    break_marker = "\x00"
    parts: list[str] = []

    def walk(e: ET.Element) -> None:
        if e.text:
            parts.append(e.text)
        for child in e:
            tag = child.tag.lower() if isinstance(child.tag, str) else ""
            if tag == "dt":
                # Margin numbers (Randnummern) — skip, but keep tail text
                if child.tail:
                    parts.append(child.tail)
                continue
            if tag == "br":
                parts.append(break_marker)
            else:
                walk(child)
                if tag in block_tags:
                    parts.append(break_marker)
            if child.tail:
                parts.append(child.tail)

    walk(el)

    paragraphs = []
    for raw_block in "".join(parts).split(break_marker):
        block = re.sub(r"\s+", " ", raw_block).strip()
        if block:
            paragraphs.append(block)
    return "\n\n".join(paragraphs)


def _format_date(yyyymmdd: str) -> str:
    """'20100108' -> '2010-01-08' (returns input unchanged if not 8 digits)."""
    if re.fullmatch(r"\d{8}", yyyymmdd):
        return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"
    return yyyymmdd


def parse_decision_xml(xml_text: str) -> dict | None:
    """Parse a decision XML into a dict with metadata and markdown text."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning("Failed to parse decision XML: %s", e)
        return None

    doknr = (root.findtext("doknr") or "").strip()
    if not doknr:
        return None

    court = (root.findtext("gertyp") or "").strip()
    panel = (root.findtext("spruchkoerper") or "").strip()
    date = _format_date((root.findtext("entsch-datum") or "").strip())
    aktenzeichen = (root.findtext("aktenzeichen") or "").strip()
    doc_type = (root.findtext("doktyp") or "").strip()
    ecli = (root.findtext("ecli") or "").strip()
    titelzeile = _block_text(root.find("titelzeile"))

    title_parts = [p for p in (court, doc_type) if p]
    title = " ".join(title_parts) if title_parts else "Entscheidung"
    if date:
        title += f" vom {date}"
    if aktenzeichen:
        title += f" — {aktenzeichen}"

    body_parts = []
    if titelzeile:
        body_parts.append(titelzeile)
    for tag, heading in _SECTIONS:
        text = _block_text(root.find(tag))
        if text:
            body_parts.append(f"## {heading}\n\n{text}")

    if not body_parts:
        return None

    return {
        "doknr": doknr,
        "court": court,
        "panel": panel,
        "date": date,
        "aktenzeichen": aktenzeichen,
        "doc_type": doc_type,
        "ecli": ecli,
        "title": title,
        "text": "\n\n".join(body_parts),
    }


def parse_toc(xml_text: str) -> list[dict]:
    """Parse the rii-toc.xml into entries with court, date, doknr, and zip link."""
    root = ET.fromstring(xml_text)
    entries = []
    for item in root.findall("item"):
        link = (item.findtext("link") or "").strip()
        if not link:
            continue
        match = re.search(r"/jb-([A-Za-z0-9]+)\.zip", link)
        doknr = match.group(1) if match else ""
        gericht = (item.findtext("gericht") or "").strip()
        entries.append({
            "court": gericht.split()[0] if gericht else "",
            "date": (item.findtext("entsch-datum") or "").strip(),
            "aktenzeichen": (item.findtext("aktenzeichen") or "").strip(),
            "link": link,
            "doknr": doknr,
        })
    return entries


def sort_toc(entries: list[dict]) -> list[dict]:
    """Priority courts (BFH) first, newest decisions first within each group."""

    def key(entry: dict):
        court = entry["court"]
        priority = PRIORITY_COURTS.index(court) if court in PRIORITY_COURTS else len(PRIORITY_COURTS)
        return (priority, -int(entry["date"] or 0))

    return sorted(entries, key=key)


def decision_url(doknr: str) -> str:
    """Canonical portal URL for a decision."""
    return (
        "https://www.rechtsprechung-im-internet.de/jportal/?quelle=jlink"
        f"&docid={doknr}&psml=bsjrsprod.psml&max=true"
    )


class RechtsprechungAdapter(SourceAdapter):
    """Fetches German federal court decisions from rechtsprechung-im-internet.de."""

    name = "rechtsprechung"

    def __init__(self):
        self.client = httpx.Client(
            timeout=30,
            follow_redirects=True,
            headers={
                "User-Agent": "SearchEngineBot/1.0 (research project; contact: search@reb00t.io)",
            },
        )

    def _fetch_toc(self) -> list[dict]:
        resp = self.client.get(TOC_URL)
        resp.raise_for_status()
        return sort_toc(parse_toc(resp.text))

    def _fetch_decision_xml(self, zip_url: str) -> str | None:
        try:
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
        toc = self._fetch_toc()
        logger.info("Found %d decisions in TOC", len(toc))

        doc_count = 0
        for entry in toc:
            if limit and doc_count >= limit:
                return

            # Skip already-ingested decisions without downloading the zip.
            doknr = entry["doknr"]
            if known_ids and doknr and f"rechtsprechung:{doknr.lower()}:0" in known_ids:
                continue

            xml_content = self._fetch_decision_xml(entry["link"])
            if not xml_content:
                continue

            decision = parse_decision_xml(xml_content)
            if not decision:
                continue

            chunks = chunk_text(decision["text"], title=decision["title"])
            for chunk_idx, chunk in enumerate(chunks):
                doc_id = f"rechtsprechung:{decision['doknr'].lower()}:{chunk_idx}"
                yield Document(
                    id=doc_id,
                    source="rechtsprechung",
                    title=decision["title"],
                    url=decision_url(decision["doknr"]),
                    language="de",
                    text=chunk,
                    metadata={
                        "court": decision["court"],
                        "panel": decision["panel"],
                        "aktenzeichen": decision["aktenzeichen"],
                        "doc_type": decision["doc_type"],
                        "ecli": decision["ecli"],
                        "decision_date": decision["date"],
                        "chunk_index": chunk_idx,
                    },
                    timestamp=decision["date"],
                )
                doc_count += 1

            time.sleep(0.2)  # rate limit

        logger.info("Extracted %d document chunks from decisions", doc_count)

    def document_url(self, doc_id: str) -> str:
        doknr = doc_id.split(":")[1]
        return decision_url(doknr.upper())
