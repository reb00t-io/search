"""arXiv source adapter — fetches papers via arXiv API."""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator

import httpx

from ingestion.base import Document, SourceAdapter

logger = logging.getLogger(__name__)

ARXIV_API = "http://export.arxiv.org/api/query"
ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

# Categories to fetch for the MVP
CATEGORIES = ["cs.AI", "cs.CL", "cs.LG", "cs.CR", "cs.SE"]


def _clean_abstract(text: str) -> str:
    """Clean up arXiv abstract text."""
    text = re.sub(r"\s+", " ", text).strip()
    # Remove LaTeX artifacts but keep basic math
    text = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\[a-zA-Z]+", "", text)
    text = re.sub(r"[{}]", "", text)
    return text


def _extract_paper_id(id_url: str) -> str:
    """Extract paper ID from arXiv URL: http://arxiv.org/abs/2301.07041v1 -> 2301.07041"""
    match = re.search(r"(\d{4}\.\d{4,5})(v\d+)?$", id_url)
    if match:
        return match.group(1)
    # Old-style IDs
    match = re.search(r"/([^/]+)$", id_url)
    return match.group(1) if match else id_url


class ArxivAdapter(SourceAdapter):
    """Fetches arXiv papers via the arXiv API."""

    name = "arxiv"

    def __init__(self, categories: list[str] | None = None):
        self.categories = categories or CATEGORIES
        self.client = httpx.Client(
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": "SearchEngine/1.0 (research project)"},
        )

    def _fetch_papers(self, query: str, start: int = 0, max_results: int = 50) -> list[dict]:
        """Fetch papers from the arXiv API."""
        params = {
            "search_query": query,
            "start": start,
            "max_results": max_results,
            "sortBy": "lastUpdatedDate",
            "sortOrder": "descending",
        }
        try:
            resp = self.client.get(ARXIV_API, params=params)
            resp.raise_for_status()
            return self._parse_atom_feed(resp.text)
        except Exception as e:
            logger.warning("arXiv API error for %s: %s", query, e)
            return []

    def _parse_atom_feed(self, xml_text: str) -> list[dict]:
        """Parse arXiv Atom feed response."""
        root = ET.fromstring(xml_text)
        papers = []
        for entry in root.findall("atom:entry", ARXIV_NS):
            paper_id_url = entry.findtext("atom:id", "", ARXIV_NS)
            paper_id = _extract_paper_id(paper_id_url)
            title = entry.findtext("atom:title", "", ARXIV_NS).strip()
            title = re.sub(r"\s+", " ", title)
            abstract = entry.findtext("atom:summary", "", ARXIV_NS).strip()
            published = entry.findtext("atom:published", "", ARXIV_NS)
            updated = entry.findtext("atom:updated", "", ARXIV_NS)

            authors = []
            for author in entry.findall("atom:author", ARXIV_NS):
                name = author.findtext("atom:name", "", ARXIV_NS)
                if name:
                    authors.append(name)

            categories = []
            for cat in entry.findall("arxiv:primary_category", ARXIV_NS):
                term = cat.get("term", "")
                if term:
                    categories.append(term)
            for cat in entry.findall("atom:category", ARXIV_NS):
                term = cat.get("term", "")
                if term and term not in categories:
                    categories.append(term)

            papers.append({
                "id": paper_id,
                "title": title,
                "abstract": abstract,
                "authors": authors,
                "categories": categories,
                "published": published,
                "updated": updated or published,
            })
        return papers

    def bulk_ingest(self, limit: int | None = None, known_ids: set[str] | None = None) -> Iterator[Document]:
        """Fetch recent papers from configured categories."""
        per_category = (limit or 50) // len(self.categories)
        per_category = max(per_category, 10)

        seen_ids = set()
        for cat in self.categories:
            query = f"cat:{cat}"
            papers = self._fetch_papers(query, max_results=per_category)
            logger.info("Fetched %d papers from %s", len(papers), cat)

            for paper in papers:
                if paper["id"] in seen_ids:
                    continue
                seen_ids.add(paper["id"])

                abstract = _clean_abstract(paper["abstract"])
                if len(abstract) < 50:
                    continue

                # Format as markdown
                authors_str = ", ".join(paper["authors"][:5])
                if len(paper["authors"]) > 5:
                    authors_str += f" et al. ({len(paper['authors'])} authors)"
                cats_str = ", ".join(paper["categories"][:5])

                text = f"# {paper['title']}\n\n"
                text += f"**Authors:** {authors_str}\n\n"
                text += f"**Categories:** {cats_str}\n\n"
                text += abstract

                doc_id = f"arxiv:{paper['id']}:0"
                yield Document(
                    id=doc_id,
                    source="arxiv",
                    title=paper["title"],
                    url=f"https://arxiv.org/abs/{paper['id']}",
                    language="en",
                    text=text,
                    metadata={
                        "authors": paper["authors"],
                        "categories": paper["categories"],
                        "chunk_index": 0,
                        "total_chunks": 1,
                    },
                    timestamp=paper["updated"],
                )

            # arXiv rate limit: 1 request per 3 seconds
            time.sleep(3)

    def document_url(self, doc_id: str) -> str:
        paper_id = doc_id.split(":")[1]
        return f"https://arxiv.org/abs/{paper_id}"
