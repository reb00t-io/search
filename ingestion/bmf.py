"""BMF-Schreiben source adapter — German tax administration guidance (PDF).

The BMF website's HTML listing pages sit behind a bot-protection wall, but two
things remain openly accessible to well-behaved clients: the sitemap.xml and
the PDF documents themselves. Discovery therefore goes through the sitemap:

1. Fetch https://www.bundesfinanzministerium.de/sitemap.xml
2. Keep URLs under Content/DE/Downloads/BMF_Schreiben/
3. Derive the PDF URL from each detail-page URL
   (".../foo.html" -> ".../foo.pdf?__blob=publicationFile&v=1")
4. Download the PDF and extract text with pypdf

The sitemap lists the current/recent Schreiben (~400); the historic archive is
not exposed there. Titles come from PDF metadata, dates from the URL slug.
BMF-Schreiben are official works (§ 5 UrhG), free to reuse.
"""

from __future__ import annotations

import io
import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator

import httpx
from pypdf import PdfReader

from ingestion.base import Document, SourceAdapter
from ingestion.chunking import chunk_text

logger = logging.getLogger(__name__)

SITEMAP_URL = "https://www.bundesfinanzministerium.de/sitemap.xml"
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
BMF_PATH_MARKER = "/Content/DE/Downloads/BMF_Schreiben/"

# Seconds between PDF downloads — the site is bot-sensitive, stay gentle.
FETCH_DELAY_SECONDS = 3.0


def pdf_url_for(html_url: str) -> str:
    """Derive the PDF download URL from a sitemap detail-page URL."""
    base = html_url[:-5] if html_url.endswith(".html") else html_url
    return f"{base}.pdf?__blob=publicationFile&v=1"


def parse_sitemap(xml_text: str) -> list[dict]:
    """Extract BMF-Schreiben entries from sitemap XML, newest first.

    Each entry has: url (detail page), slug, date (ISO or ""), category.
    """
    root = ET.fromstring(xml_text)
    entries = []
    for loc in root.findall(".//sm:url/sm:loc", SITEMAP_NS):
        url = (loc.text or "").strip()
        if BMF_PATH_MARKER not in url or not url.endswith(".html"):
            continue

        rel_path = url.split(BMF_PATH_MARKER, 1)[1]
        parts = rel_path.split("/")
        filename = parts[-1][:-5]  # strip ".html"
        category = "/".join(parts[:-1])

        slug = re.sub(r"[^a-z0-9-]+", "-", filename.lower()).strip("-")
        if not slug:
            continue

        date_match = re.match(r"(\d{4}-\d{2}-\d{2})", filename)
        entries.append({
            "url": url,
            "slug": slug,
            "date": date_match.group(1) if date_match else "",
            "category": category,
        })

    entries.sort(key=lambda e: e["date"], reverse=True)
    return entries


def clean_pdf_text(text: str) -> str:
    """Normalize pypdf output: drop whitespace-only lines, collapse spacing."""
    lines = []
    for raw_line in text.splitlines():
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        lines.append(line)
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def extract_pdf(pdf_bytes: bytes) -> tuple[str, str]:
    """Extract (title, text) from PDF bytes. Empty strings on failure."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        title = ""
        if reader.metadata and reader.metadata.title:
            title = str(reader.metadata.title).strip()
        pages = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                pages.append(page_text)
        return title, clean_pdf_text("\n\n".join(pages))
    except Exception as e:
        logger.warning("Failed to extract PDF text: %s", e)
        return "", ""


def extract_gz(text: str) -> str:
    """Extract the Geschäftszeichen (GZ) from the letterhead, if present."""
    match = re.search(r"GZ:\s*([^\n]+)", text[:3000])
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""


class BmfAdapter(SourceAdapter):
    """Fetches BMF-Schreiben PDFs from bundesfinanzministerium.de."""

    name = "bmf"

    def __init__(self):
        self.client = httpx.Client(
            timeout=60,
            follow_redirects=True,
            headers={
                "User-Agent": "SearchEngineBot/1.0 (research project; contact: search@reb00t.io)",
            },
        )

    def _fetch_sitemap(self) -> list[dict]:
        resp = self.client.get(SITEMAP_URL)
        resp.raise_for_status()
        return parse_sitemap(resp.text)

    def _fetch_pdf(self, url: str) -> bytes | None:
        try:
            resp = self.client.get(url)
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")
            if "pdf" not in content_type:
                logger.warning("Not a PDF (%s): %s", content_type, url)
                return None
            return resp.content
        except Exception as e:
            logger.warning("Failed to fetch %s: %s", url, e)
            return None

    def bulk_ingest(self, limit: int | None = None, known_ids: set[str] | None = None) -> Iterator[Document]:
        entries = self._fetch_sitemap()
        logger.info("Found %d BMF-Schreiben in sitemap", len(entries))

        doc_count = 0
        for entry in entries:
            if limit and doc_count >= limit:
                return

            if known_ids and f"bmf:{entry['slug']}:0" in known_ids:
                continue

            pdf_url = pdf_url_for(entry["url"])
            pdf_bytes = self._fetch_pdf(pdf_url)
            if not pdf_bytes:
                continue

            title, text = extract_pdf(pdf_bytes)
            if not text or len(text) < 100:
                logger.warning("No usable text in %s", pdf_url)
                continue
            if not title:
                title = entry["slug"].replace("-", " ")

            chunks = chunk_text(text, title=title)
            for chunk_idx, chunk in enumerate(chunks):
                yield Document(
                    id=f"bmf:{entry['slug']}:{chunk_idx}",
                    source="bmf",
                    title=title,
                    url=pdf_url,
                    language="de",
                    text=chunk,
                    metadata={
                        "category": entry["category"],
                        "gz": extract_gz(text),
                        "chunk_index": chunk_idx,
                    },
                    timestamp=entry["date"],
                )
                doc_count += 1

            time.sleep(FETCH_DELAY_SECONDS)

        logger.info("Extracted %d document chunks from BMF-Schreiben", doc_count)

    def document_url(self, doc_id: str) -> str:
        return ""  # URL is stored per document (path is not derivable from the slug)
