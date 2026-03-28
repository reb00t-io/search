"""Shared text chunking utilities for all source adapters.

Splits text into chunks suitable for embedding (~500-1000 tokens each),
respecting heading and paragraph boundaries where possible.
"""

from __future__ import annotations

import re

# Target ~800 words per chunk, hard max ~1500 words
DEFAULT_TARGET_WORDS = 800
MAX_CHUNK_WORDS = 1500


def chunk_text(text: str, title: str = "", target_words: int = DEFAULT_TARGET_WORDS) -> list[str]:
    """Split text into chunks at heading/paragraph/sentence boundaries.

    1. Split at ## headings
    2. If any section exceeds max, split at paragraphs
    3. If any paragraph still exceeds max, split at sentence boundaries
    4. Prepend title to first chunk
    """
    prefix = f"# {title}\n\n" if title else ""

    if not text.strip():
        return [prefix.strip()] if prefix.strip() else []

    # Split at markdown headings
    sections = re.split(r"(?=^#{1,4} )", text, flags=re.MULTILINE)
    sections = [s.strip() for s in sections if s.strip()]

    if not sections:
        sections = [text]

    # Split oversized sections into smaller pieces
    pieces = []
    for section in sections:
        if len(section.split()) <= target_words:
            pieces.append(section)
        else:
            pieces.extend(_split_recursively(section, target_words))

    # Merge small pieces into chunks of ~target_words
    chunks = []
    current = prefix

    for piece in pieces:
        piece_words = len(piece.split())
        current_words = len(current.split())

        if current_words + piece_words > target_words and current_words > 50:
            chunks.append(current.strip())
            current = piece + "\n\n"
        else:
            current += piece + "\n\n"

    if current.strip():
        chunks.append(current.strip())

    # Final safety: hard-split anything still over max
    final = []
    for chunk in chunks:
        if len(chunk.split()) > MAX_CHUNK_WORDS:
            final.extend(_hard_split(chunk, MAX_CHUNK_WORDS))
        else:
            final.append(chunk)

    return final if final else [prefix + text]


def _split_recursively(text: str, target_words: int) -> list[str]:
    """Split text at paragraphs, then sentences, to fit within target_words."""
    # Try paragraph boundaries first
    paragraphs = re.split(r"\n{2,}", text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    if len(paragraphs) > 1:
        pieces = _merge_pieces(paragraphs, target_words)
        # Recursively split any still-oversized pieces
        result = []
        for piece in pieces:
            if len(piece.split()) > target_words:
                result.extend(_split_at_sentences(piece, target_words))
            else:
                result.append(piece)
        return result

    # Single paragraph — split at sentences
    return _split_at_sentences(text, target_words)


def _split_at_sentences(text: str, target_words: int) -> list[str]:
    """Split text at sentence boundaries."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    if len(sentences) <= 1:
        # No sentence boundaries — hard split
        return _hard_split(text, target_words)
    return _merge_pieces(sentences, target_words)


def _merge_pieces(pieces: list[str], target_words: int) -> list[str]:
    """Merge small pieces into chunks of ~target_words."""
    result = []
    current = ""
    for piece in pieces:
        if len(current.split()) + len(piece.split()) > target_words and len(current.split()) > 30:
            result.append(current.strip())
            current = piece + "\n\n"
        else:
            current += piece + "\n\n"
    if current.strip():
        result.append(current.strip())
    return result


def _hard_split(text: str, max_words: int) -> list[str]:
    """Last resort: split at word boundaries."""
    words = text.split()
    chunks = []
    for i in range(0, len(words), max_words):
        chunks.append(" ".join(words[i : i + max_words]))
    return chunks
