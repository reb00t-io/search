"""RKI (Robert Koch Institut) source adapter via OAI-PMH repository."""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator

import httpx

from ingestion.base import Document, SourceAdapter

logger = logging.getLogger(__name__)

OAI_BASE = "https://edoc.rki.de/oai/request"
OAI_NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "oai_dc": "http://www.openarchives.org/OAI/2.0/oai_dc/",
}


class RkiAdapter(SourceAdapter):
    """Fetches RKI publications via OAI-PMH (edoc.rki.de)."""

    name = "rki"

    def __init__(self):
        self.client = httpx.Client(
            timeout=30,
            follow_redirects=True,
            headers={
                "User-Agent": "SearchEngineBot/1.0 (research project; contact: search@reb00t.io)",
            },
        )

    def _fetch_page(self, resumption_token: str | None = None) -> tuple[list[dict], str | None]:
        """Fetch one page of OAI-PMH records. Returns (records, next_token)."""
        if resumption_token:
            params = {"verb": "ListRecords", "resumptionToken": resumption_token}
        else:
            params = {
                "verb": "ListRecords",
                "metadataPrefix": "oai_dc",
            }

        try:
            resp = self.client.get(OAI_BASE, params=params)
            resp.raise_for_status()
        except Exception as e:
            logger.warning("RKI OAI-PMH error: %s", e)
            return [], None

        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as e:
            logger.warning("RKI OAI-PMH XML parse error: %s", e)
            return [], None

        records = []
        for record in root.findall(".//oai:record", OAI_NS):
            header = record.find("oai:header", OAI_NS)
            if header is not None and header.get("status") == "deleted":
                continue

            metadata = record.find(".//oai_dc:dc", OAI_NS)
            if metadata is None:
                # Try without namespace prefix
                metadata = record.find(".//{http://www.openarchives.org/OAI/2.0/oai_dc/}dc")
            if metadata is None:
                continue

            def _dc(tag: str) -> str:
                el = metadata.find(f"{{http://purl.org/dc/elements/1.1/}}{tag}")
                return (el.text or "").strip() if el is not None else ""

            def _dc_all(tag: str) -> list[str]:
                return [
                    (el.text or "").strip()
                    for el in metadata.findall(f"{{http://purl.org/dc/elements/1.1/}}{tag}")
                    if el.text
                ]

            title = _dc("title")
            if not title:
                continue

            description = _dc("description")
            creators = _dc_all("creator")
            subjects = _dc_all("subject")
            date = _dc("date")
            language = _dc("language") or "de"
            identifier = _dc("identifier")
            doc_type = _dc("type")

            # Extract URL from identifier
            url = ""
            for ident in _dc_all("identifier"):
                if ident.startswith("http"):
                    url = ident
                    break

            records.append({
                "title": title,
                "description": description,
                "creators": creators,
                "subjects": subjects,
                "date": date,
                "language": language,
                "url": url,
                "type": doc_type,
            })

        # Get resumption token
        token_el = root.find(".//oai:resumptionToken", OAI_NS)
        next_token = None
        if token_el is not None and token_el.text:
            next_token = token_el.text.strip()

        return records, next_token

    def bulk_ingest(self, limit: int | None = None) -> Iterator[Document]:
        """Fetch all RKI publications via OAI-PMH."""
        doc_count = 0
        token = None
        page = 0

        while True:
            records, next_token = self._fetch_page(token)
            page += 1
            logger.info("RKI OAI-PMH page %d: %d records", page, len(records))

            for record in records:
                if limit and doc_count >= limit:
                    return

                title = record["title"]
                description = record["description"]

                # Build markdown content
                text = f"# {title}\n\n"
                if record["creators"]:
                    text += f"**Autoren:** {', '.join(record['creators'])}\n\n"
                if record["subjects"]:
                    # Filter out DDC codes
                    subjects = [s for s in record["subjects"] if not s.startswith("ddc:")]
                    if subjects:
                        text += f"**Themen:** {', '.join(subjects)}\n\n"
                if record["type"]:
                    text += f"**Typ:** {record['type']}\n\n"
                if description:
                    text += description

                if len(text) < 100:
                    continue

                # Map language codes
                lang = record["language"]
                if lang in ("ger", "deu"):
                    lang = "de"
                elif lang in ("eng",):
                    lang = "en"

                # Create stable ID from URL or title
                if record["url"]:
                    doc_id_slug = re.sub(r"[^a-z0-9]+", "-", record["url"].split("/")[-1].lower())
                else:
                    doc_id_slug = re.sub(r"[^a-z0-9]+", "-", title.lower())[:60]

                doc_id = f"rki:{doc_id_slug}:0"
                yield Document(
                    id=doc_id,
                    source="rki",
                    title=title,
                    url=record["url"] or f"https://edoc.rki.de",
                    language=lang,
                    text=text,
                    metadata={
                        "creators": record["creators"],
                        "subjects": record["subjects"],
                        "type": record["type"],
                        "chunk_index": 0,
                        "total_chunks": 1,
                    },
                    timestamp=record["date"],
                )
                doc_count += 1

            if not next_token:
                break
            token = next_token
            time.sleep(1)  # OAI-PMH rate limit

        logger.info("RKI: %d publications fetched", doc_count)

    def document_url(self, doc_id: str) -> str:
        return "https://edoc.rki.de"
