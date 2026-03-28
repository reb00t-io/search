"""PubMed source adapter — open-access biomedical literature."""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator

import httpx

from ingestion.base import Document, SourceAdapter

logger = logging.getLogger(__name__)

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

# Broad search terms to get a diverse set of open-access articles
SEARCH_QUERIES = [
    "machine learning",
    "artificial intelligence medicine",
    "genomics",
    "public health",
    "drug discovery",
    "neuroscience",
    "climate health",
    "epidemiology",
    "cancer treatment",
    "mental health",
]


class PubmedAdapter(SourceAdapter):
    """Fetches biomedical article abstracts from PubMed."""

    name = "pubmed"

    def __init__(self, queries: list[str] | None = None):
        self.queries = queries or SEARCH_QUERIES
        self.client = httpx.Client(
            timeout=30,
            follow_redirects=True,
            headers={
                "User-Agent": "SearchEngineBot/1.0 (research project; contact: search@reb00t.io)",
            },
        )

    def _search_ids(self, query: str, max_results: int = 20) -> list[str]:
        """Search PubMed and return article PMIDs."""
        params = {
            "db": "pubmed",
            "term": query,
            "retmax": max_results,
            "retmode": "json",
            "sort": "relevance",
        }
        try:
            resp = self.client.get(ESEARCH_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            return data.get("esearchresult", {}).get("idlist", [])
        except Exception as e:
            logger.warning("PubMed search error for '%s': %s", query, e)
            return []

    def _fetch_articles(self, pmids: list[str]) -> list[dict]:
        """Fetch article details for a list of PMIDs."""
        if not pmids:
            return []
        params = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "rettype": "xml",
            "retmode": "xml",
        }
        try:
            resp = self.client.get(EFETCH_URL, params=params)
            resp.raise_for_status()
            return self._parse_articles_xml(resp.text)
        except Exception as e:
            logger.warning("PubMed fetch error: %s", e)
            return []

    def _parse_articles_xml(self, xml_text: str) -> list[dict]:
        """Parse PubMed XML response into article dicts."""
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning("PubMed XML parse error: %s", e)
            return []

        articles = []
        for article_el in root.findall(".//PubmedArticle"):
            medline = article_el.find("MedlineCitation")
            if medline is None:
                continue

            pmid_el = medline.find("PMID")
            pmid = pmid_el.text.strip() if pmid_el is not None and pmid_el.text else ""
            if not pmid:
                continue

            article = medline.find("Article")
            if article is None:
                continue

            title = article.findtext("ArticleTitle", "").strip()

            # Abstract may have labeled sections
            abstract_parts = []
            abstract_el = article.find("Abstract")
            if abstract_el is not None:
                for text_el in abstract_el.findall("AbstractText"):
                    label = text_el.get("Label", "")
                    text = (text_el.text or "").strip()
                    if len(text_el) > 0:
                        # Get text from child elements too
                        text = "".join(text_el.itertext()).strip()
                    if label and text:
                        abstract_parts.append(f"**{label}:** {text}")
                    elif text:
                        abstract_parts.append(text)

            abstract = "\n\n".join(abstract_parts)
            if not abstract:
                continue

            # Authors
            authors = []
            author_list = article.find("AuthorList")
            if author_list is not None:
                for author_el in author_list.findall("Author"):
                    last = author_el.findtext("LastName", "")
                    first = author_el.findtext("ForeName", "")
                    if last:
                        authors.append(f"{first} {last}".strip())

            # Journal
            journal = ""
            journal_el = article.find("Journal")
            if journal_el is not None:
                journal = journal_el.findtext("Title", "")

            # Date
            pub_date = ""
            date_el = article.find(".//PubDate")
            if date_el is not None:
                year = date_el.findtext("Year", "")
                month = date_el.findtext("Month", "")
                if year:
                    pub_date = f"{year}-{month}-01" if month else f"{year}-01-01"

            # DOI
            doi = ""
            for eid in article.findall("ELocationID"):
                if eid.get("EIdType") == "doi" and eid.text:
                    doi = eid.text.strip()

            # MeSH terms
            mesh_terms = []
            for mesh in medline.findall(".//MeshHeading/DescriptorName"):
                if mesh.text:
                    mesh_terms.append(mesh.text.strip())

            # Keywords
            keywords = []
            for kw in medline.findall(".//Keyword"):
                if kw.text:
                    keywords.append(kw.text.strip())

            articles.append({
                "pmid": pmid,
                "title": title,
                "abstract": abstract,
                "authors": authors,
                "journal": journal,
                "date": pub_date,
                "doi": doi,
                "mesh_terms": mesh_terms,
                "keywords": keywords,
            })

        return articles

    def bulk_ingest(self, limit: int | None = None, known_ids: set[str] | None = None) -> Iterator[Document]:
        """Fetch articles from PubMed across multiple search queries."""
        per_query = max((limit or 50) // len(self.queries), 5)
        seen_pmids = set()

        for query in self.queries:
            pmids = self._search_ids(query, max_results=per_query)
            new_pmids = [p for p in pmids if p not in seen_pmids]
            if not new_pmids:
                continue

            articles = self._fetch_articles(new_pmids)
            logger.info("Fetched %d articles for '%s'", len(articles), query)

            for article in articles:
                if article["pmid"] in seen_pmids:
                    continue
                seen_pmids.add(article["pmid"])

                # Format as markdown
                authors_str = ", ".join(article["authors"][:5])
                if len(article["authors"]) > 5:
                    authors_str += f" et al. ({len(article['authors'])} authors)"

                text = f"# {article['title']}\n\n"
                if authors_str:
                    text += f"**Authors:** {authors_str}\n\n"
                if article["journal"]:
                    text += f"**Journal:** {article['journal']}\n\n"
                text += article["abstract"]

                url = f"https://pubmed.ncbi.nlm.nih.gov/{article['pmid']}/"
                if article["doi"]:
                    url = f"https://doi.org/{article['doi']}"

                doc_id = f"pubmed:{article['pmid']}:0"
                yield Document(
                    id=doc_id,
                    source="pubmed",
                    title=article["title"],
                    url=url,
                    language="en",
                    text=text,
                    metadata={
                        "authors": article["authors"],
                        "journal": article["journal"],
                        "mesh_terms": article["mesh_terms"],
                        "keywords": article["keywords"],
                        "doi": article["doi"],
                        "chunk_index": 0,
                        "total_chunks": 1,
                    },
                    timestamp=article["date"],
                )

            # Rate limit: 3 req/sec without API key
            time.sleep(1)

    def document_url(self, doc_id: str) -> str:
        pmid = doc_id.split(":")[1]
        return f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
