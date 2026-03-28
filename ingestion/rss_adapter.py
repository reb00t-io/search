"""RSS feed source adapter — fetches articles from RSS feeds and extracts content.

Used for Tagesschau, Deutsche Welle, and other news sources.
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from ingestion.base import Document, SourceAdapter
from ingestion.chunking import chunk_text

logger = logging.getLogger(__name__)

# Namespaces commonly used in RSS feeds
RSS_NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}

# --- Feed configurations ---

TAGESSCHAU_FEEDS = [
    "https://www.tagesschau.de/index~rss2.xml",
    "https://www.tagesschau.de/xml/rss2_https/",
]

DW_FEEDS = [
    "https://rss.dw.com/xml/rss-de-all",
]


def _parse_rss_items(xml_text: str) -> list[dict]:
    """Parse RSS 2.0 feed into item dicts."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning("RSS parse error: %s", e)
        return []

    items = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = (item.findtext("description") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        category = (item.findtext("category") or "").strip()

        # Try content:encoded for full article text
        content_encoded = (item.findtext("content:encoded", "", RSS_NS) or "").strip()

        if not title and not link:
            continue

        items.append({
            "title": title,
            "link": link,
            "description": description,
            "content": content_encoded,
            "pub_date": pub_date,
            "category": category,
        })

    return items


def _html_to_markdown(html: str) -> str:
    """Convert HTML content to clean markdown text."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")

    # Remove scripts, styles, images
    for tag in soup(["script", "style", "noscript", "img", "figure", "figcaption"]):
        tag.decompose()

    # Convert headers
    for i in range(1, 7):
        for h in soup.find_all(f"h{i}"):
            h.replace_with(f"\n\n{'#' * i} {h.get_text(strip=True)}\n\n")

    # Convert links
    for a in soup.find_all("a"):
        href = a.get("href", "")
        text = a.get_text(strip=True)
        if href and text:
            a.replace_with(f"[{text}]({href})")
        elif text:
            a.replace_with(text)

    # Convert bold/strong
    for tag in soup.find_all(["strong", "b"]):
        tag.replace_with(f"**{tag.get_text(strip=True)}**")

    # Convert lists
    for li in soup.find_all("li"):
        li.replace_with(f"\n- {li.get_text(strip=True)}")

    text = soup.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_article_id(url: str) -> str:
    """Extract a stable ID from a URL."""
    parsed = urlparse(url)
    # Use path as ID, cleaned up
    path = parsed.path.strip("/").replace("/", "-")
    path = re.sub(r"[^a-z0-9-]", "", path.lower())
    return path[:80] or "article"


class RssAdapter(SourceAdapter):
    """Generic RSS feed adapter."""

    name = "rss"

    def __init__(self, source_name: str, feeds: list[str], language: str = "de"):
        self.source_name = source_name
        self.feeds = feeds
        self.language = language
        self.client = httpx.Client(
            timeout=30,
            follow_redirects=True,
            headers={
                "User-Agent": "SearchEngineBot/1.0 (research project; contact: search@reb00t.io)",
            },
        )

    def _fetch_feed(self, url: str) -> list[dict]:
        """Fetch and parse a single RSS feed."""
        try:
            resp = self.client.get(url)
            resp.raise_for_status()
            return _parse_rss_items(resp.text)
        except Exception as e:
            logger.warning("Failed to fetch feed %s: %s", url, e)
            return []

    def _fetch_article_content(self, url: str) -> str:
        """Fetch full article HTML and convert to markdown."""
        try:
            resp = self.client.get(url)
            resp.raise_for_status()
            return _html_to_markdown(resp.text)
        except Exception as e:
            logger.debug("Failed to fetch article %s: %s", url, e)
            return ""

    def bulk_ingest(self, limit: int | None = None) -> Iterator[Document]:
        """Fetch articles from all configured RSS feeds."""
        seen_urls = set()
        doc_count = 0

        for feed_url in self.feeds:
            items = self._fetch_feed(feed_url)
            logger.info("Fetched %d items from %s", len(items), feed_url)

            for item in items:
                if limit and doc_count >= limit:
                    return

                url = item["link"]
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                # Use content:encoded if available, otherwise fetch the page
                content = ""
                if item["content"]:
                    content = _html_to_markdown(item["content"])

                if not content and item["description"]:
                    content = _html_to_markdown(item["description"])

                if not content:
                    # Fetch full article
                    content = self._fetch_article_content(url)
                    time.sleep(0.3)  # rate limit

                if not content or len(content) < 100:
                    continue

                article_id = _extract_article_id(url)
                title = item["title"] or "Untitled"

                # Build markdown
                text = f"# {title}\n\n{content}"

                # Chunk if needed
                chunks = chunk_text(text, title="")
                for i, chunk_text_str in enumerate(chunks):
                    doc_id = f"{self.source_name}:{article_id}:{i}"
                    yield Document(
                        id=doc_id,
                        source=self.source_name,
                        title=title,
                        url=url,
                        language=self.language,
                        text=chunk_text_str,
                        metadata={
                            "category": item.get("category", ""),
                            "chunk_index": i,
                            "total_chunks": len(chunks),
                        },
                        timestamp=item.get("pub_date", ""),
                    )
                    doc_count += 1

    def document_url(self, doc_id: str) -> str:
        return ""  # URL is stored per document


class TagesschauAdapter(RssAdapter):
    """Tagesschau.de news adapter."""
    name = "tagesschau"

    def __init__(self):
        super().__init__(
            source_name="tagesschau",
            feeds=TAGESSCHAU_FEEDS,
            language="de",
        )


class DWAdapter(RssAdapter):
    """Deutsche Welle German news adapter."""
    name = "dw"

    def __init__(self):
        super().__init__(
            source_name="dw",
            feeds=DW_FEEDS,
            language="de",
        )
