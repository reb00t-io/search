"""Quality and safety filters for ingested documents."""

from __future__ import annotations

import re
from dataclasses import dataclass

# --- Prompt injection patterns ---
INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts|commands)",
        r"disregard\s+(all\s+)?(previous|prior|above)",
        r"you\s+are\s+now\s+(a|an|the)\b",
        r"^system\s*:",
        r"^assistant\s*:",
        r"^user\s*:",
        r"\bdo\s+not\s+follow\s+(any|your)\s+(previous|prior)",
        r"new\s+instructions?\s*:",
        r"override\s+(previous|prior|system)",
        r"forget\s+(all|everything|your)\s+(previous|prior|instructions)",
    ]
]

# Zero-width and homoglyph characters that could be used for hidden text
SUSPICIOUS_CHARS = re.compile(
    r"[\u200b\u200c\u200d\u200e\u200f\u2060\u2061\u2062\u2063\u2064\ufeff]"
)

# Base64-encoded payload heuristic (long base64 strings in otherwise normal text)
BASE64_PAYLOAD = re.compile(r"[A-Za-z0-9+/]{100,}={0,2}")


@dataclass
class FilterResult:
    accepted: bool
    reason: str = ""


def check_quality(text: str, min_length: int = 200) -> FilterResult:
    """Check document quality. Returns FilterResult."""
    if len(text.strip()) < min_length:
        return FilterResult(False, f"too_short: {len(text.strip())} chars < {min_length}")

    # Check prose ratio: count sentences (periods followed by space/newline)
    sentences = re.findall(r"[.!?]\s", text)
    words = text.split()
    if len(words) > 20 and len(sentences) < 2:
        # Very few sentences relative to word count — likely a list or table only
        lines = text.strip().splitlines()
        list_lines = sum(1 for l in lines if re.match(r"^\s*[-*#\d]", l))
        if list_lines > len(lines) * 0.8:
            return FilterResult(False, "low_prose_ratio: mostly lists")

    return FilterResult(True)


def check_safety(text: str) -> FilterResult:
    """Check for prompt injection patterns. Returns FilterResult."""
    for pattern in INJECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            return FilterResult(False, f"injection_pattern: {match.group()[:80]}")

    # Check for suspicious hidden characters
    suspicious = SUSPICIOUS_CHARS.findall(text)
    if len(suspicious) > 5:
        return FilterResult(False, f"suspicious_chars: {len(suspicious)} zero-width characters")

    # Check for base64 payloads
    b64_matches = BASE64_PAYLOAD.findall(text)
    if b64_matches:
        # Allow if it looks like a code block or URL
        for match in b64_matches:
            if len(match) > 200:
                return FilterResult(False, f"base64_payload: {len(match)} chars")

    return FilterResult(True)


def filter_document(text: str, min_length: int = 200) -> FilterResult:
    """Run all filters on a document. Returns first failing result or accepted."""
    quality = check_quality(text, min_length)
    if not quality.accepted:
        return quality

    safety = check_safety(text)
    if not safety.accepted:
        return safety

    return FilterResult(True)
