"""Wikipedia source adapter — fetches articles via MediaWiki API."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterator

import httpx
import mwparserfromhell

from ingestion.base import Document, SourceAdapter
from ingestion.chunking import chunk_text

logger = logging.getLogger(__name__)

# Popular articles to seed the index — a mix of topics
SEED_ARTICLES = {
    "de": [
        "Deutschland", "Berlin", "Albert_Einstein", "Mathematik", "Physik",
        "Informatik", "Künstliche_Intelligenz", "Maschinelles_Lernen",
        "Quantencomputer", "Klimawandel", "Europäische_Union",
        "Philosophie", "Geschichte_Deutschlands", "Wirtschaft",
        "Biologie", "Chemie", "Medizin", "Musik", "Literatur",
        "Astronomie", "Sonne", "Mond", "Mars_(Planet)", "Wasser",
        "Computer", "Internet", "Programmiersprache", "Algorithmus",
        "Datenbank", "Betriebssystem", "Linux", "Python_(Programmiersprache)",
        "Elektrizität", "Magnetismus", "Relativitätstheorie",
        "Evolution", "Genetik", "Zelle_(Biologie)", "Photosynthese",
        "Demokratie", "Menschenrechte", "Vereinte_Nationen",
        "Zweiter_Weltkrieg", "Kalter_Krieg", "Globalisierung",
        "Nachhaltigkeit", "Erneuerbare_Energie", "Kernenergie",
        "Blockchain", "Kryptowährung",
    ],
    "en": [
        "Artificial_intelligence", "Machine_learning", "Deep_learning",
        "Natural_language_processing", "Computer_science", "Algorithm",
        "Database", "Operating_system", "Linux", "Python_(programming_language)",
        "Quantum_computing", "Climate_change", "Renewable_energy",
        "Nuclear_energy", "Blockchain", "Cryptocurrency",
        "Mathematics", "Physics", "Chemistry", "Biology",
        "Medicine", "Genetics", "Evolution", "Cell_(biology)",
        "Astronomy", "Solar_System", "Black_hole", "Big_Bang",
        "Philosophy", "Democracy", "Human_rights", "United_Nations",
        "World_War_II", "Cold_War", "Globalization",
        "Albert_Einstein", "Isaac_Newton", "Charles_Darwin",
        "Internet", "World_Wide_Web", "Encryption",
        "Relativity", "Quantum_mechanics", "Electromagnetism",
        "Photosynthesis", "DNA", "Protein", "Neuroscience",
        "Economics", "Sustainability", "European_Union",
        "Programming_language", "Software_engineering",
    ],
}


def _wikitext_to_markdown(wikitext: str) -> str:
    """Convert wikitext to clean markdown using mwparserfromhell for template removal."""
    # Use mwparserfromhell to strip templates only
    parsed = mwparserfromhell.parse(wikitext)
    for template in parsed.filter_templates():
        try:
            parsed.remove(template)
        except ValueError:
            pass
    text = str(parsed)

    # Remove references like <ref>...</ref> and <ref ... />
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.DOTALL)
    text = re.sub(r"<ref[^/]*/\s*>", "", text)

    # Remove remaining HTML-like tags
    text = re.sub(r"<[^>]+>", "", text)

    # Remove category links (must come before general link conversion)
    text = re.sub(r"\[\[(?:Kategorie|Category):[^\]]+\]\]", "", text)

    # Remove file/image links
    text = re.sub(r"\[\[(?:Datei|File|Bild|Image):[^\]]+\]\]", "", text)

    # Convert headings: ==H2== -> ## H2 (process from deepest to shallowest)
    text = re.sub(r"={6}\s*(.+?)\s*={6}", r"###### \1", text)
    text = re.sub(r"={5}\s*(.+?)\s*={5}", r"##### \1", text)
    text = re.sub(r"={4}\s*(.+?)\s*={4}", r"#### \1", text)
    text = re.sub(r"={3}\s*(.+?)\s*={3}", r"### \1", text)
    text = re.sub(r"={2}\s*(.+?)\s*={2}", r"## \1", text)

    # Convert bold/italic (bold first since ''' contains '')
    text = re.sub(r"'{3}(.+?)'{3}", r"**\1**", text)
    text = re.sub(r"'{2}(.+?)'{2}", r"*\1*", text)

    # Convert links: [[Target|Label]] -> Label, [[Target]] -> Target
    text = re.sub(r"\[\[[^]]*\|([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)

    # Convert wiki numbered lists (^# but not ^## which is a heading now)
    text = re.sub(r"^\*\s*", "- ", text, flags=re.MULTILINE)
    text = re.sub(r"^#(?!#)\s*", "1. ", text, flags=re.MULTILINE)

    # Remove external links markup
    text = re.sub(r"\[https?://[^\s\]]+ ([^\]]+)\]", r"\1", text)
    text = re.sub(r"\[https?://[^\]]+\]", "", text)

    # Clean up whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)

    return text.strip()


def _chunk_text(text: str, title: str, max_tokens: int = 800) -> list[str]:
    """Split text into chunks at heading/paragraph/sentence boundaries."""
    return chunk_text(text, title=title, target_words=max_tokens)


class WikipediaAdapter(SourceAdapter):
    """Fetches Wikipedia articles via MediaWiki API."""

    name = "wiki"

    def __init__(self, languages: list[str] | None = None):
        self.languages = languages or ["de", "en"]
        self.client = httpx.Client(
            timeout=30,
            follow_redirects=True,
            headers={
                "User-Agent": "SearchEngineBot/1.0 (research project; contact: search@reb00t.io)",
                "Accept": "application/json",
            },
        )

    def _api_url(self, lang: str) -> str:
        return f"https://{lang}.wikipedia.org/w/api.php"

    def _fetch_article(self, lang: str, title: str) -> dict | None:
        """Fetch a single article's wikitext and metadata."""
        params = {
            "action": "query",
            "titles": title,
            "prop": "revisions|categories|info",
            "rvprop": "content|timestamp",
            "rvslots": "main",
            "cllimit": "20",
            "format": "json",
            "formatversion": "2",
        }
        try:
            resp = self.client.get(self._api_url(lang), params=params)
            resp.raise_for_status()
            data = resp.json()
            pages = data.get("query", {}).get("pages", [])
            if not pages or pages[0].get("missing"):
                return None
            return pages[0]
        except Exception as e:
            logger.warning("Failed to fetch %s:%s: %s", lang, title, e)
            return None

    def _resolve_page_ids(self, lang: str, titles: list[str]) -> dict[str, int]:
        """Batch-resolve page IDs for titles (50 at a time, no content fetched)."""
        result = {}
        for i in range(0, len(titles), 50):
            batch = titles[i : i + 50]
            params = {
                "action": "query", "titles": "|".join(batch),
                "format": "json", "formatversion": "2",
            }
            try:
                resp = self.client.get(self._api_url(lang), params=params)
                resp.raise_for_status()
                for page in resp.json().get("query", {}).get("pages", []):
                    if not page.get("missing"):
                        result[page["title"]] = page["pageid"]
            except Exception as e:
                logger.warning("Failed to resolve page IDs for %s: %s", lang, e)
        return result

    def bulk_ingest(self, limit: int | None = None, known_ids: set[str] | None = None) -> Iterator[Document]:
        """Fetch seed articles from Wikipedia API."""
        known_ids = known_ids or set()
        known_prefixes = {":".join(kid.split(":")[:3]) for kid in known_ids if kid.startswith("wiki:")}

        for lang in self.languages:
            titles = SEED_ARTICLES.get(lang, [])
            if limit:
                titles = titles[: limit // len(self.languages)]

            # Batch-resolve page IDs to skip known articles without fetching content
            if known_prefixes:
                title_to_pid = self._resolve_page_ids(lang, titles)
                # Build reverse map: seed_title -> page_id (handle title normalization)
                pid_by_seed = {}
                for seed_title in titles:
                    # Try exact match, then with spaces
                    pid = title_to_pid.get(seed_title) or title_to_pid.get(seed_title.replace("_", " "))
                    if pid is not None:
                        pid_by_seed[seed_title] = pid
                before = len(titles)
                titles = [t for t in titles if f"wiki:{lang}:{pid_by_seed.get(t, -1)}" not in known_prefixes]
                if before > len(titles):
                    logger.info("  %s %s: skipping %d known articles, %d remaining", self.name, lang, before - len(titles), len(titles))

            for title in titles:
                page = self._fetch_article(lang, title)
                if page is None:
                    continue

                revisions = page.get("revisions", [])
                if not revisions:
                    continue

                wikitext = revisions[0].get("slots", {}).get("main", {}).get("content", "")
                timestamp = revisions[0].get("timestamp", "")
                page_id = page.get("pageid", 0)
                page_title = page.get("title", title)
                categories = [
                    c["title"].replace("Kategorie:", "").replace("Category:", "")
                    for c in page.get("categories", [])
                ]

                # Skip redirects
                if wikitext.strip().upper().startswith("#REDIRECT"):
                    continue

                markdown = _wikitext_to_markdown(wikitext)
                if len(markdown) < 100:
                    continue

                chunks = _chunk_text(markdown, page_title)
                for i, chunk in enumerate(chunks):
                    doc_id = f"wiki:{lang}:{page_id}:{i}"
                    yield Document(
                        id=doc_id,
                        source="wiki",
                        title=page_title,
                        url=f"https://{lang}.wikipedia.org/wiki/{title}",
                        language=lang,
                        text=chunk,
                        metadata={
                            "categories": categories,
                            "chunk_index": i,
                            "total_chunks": len(chunks),
                        },
                        timestamp=timestamp,
                    )

                # Be polite to the API
                time.sleep(0.1)

    def document_url(self, doc_id: str) -> str:
        parts = doc_id.split(":")
        lang = parts[1]
        return f"https://{lang}.wikipedia.org/?curid={parts[2]}"
