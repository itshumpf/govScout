"""Structured field extraction from solicitation text.

Pure regex-based extractors for NSNs, part numbers, and quantities —
the fields a quoter needs to price a DLA-style line item. No I/O.
"""

from __future__ import annotations

import re

# National Stock Number: 13 digits rendered 0000-00-000-0000.
NSN_RE = re.compile(r"\b\d{4}-\d{2}-\d{3}-\d{4}\b")

# Part numbers appear after labels like "P/N", "PN", "part number", "part no".
# The leading \b stops the label from matching mid-word, and the trailing
# negative lookahead stops it from matching as a *prefix* of a longer word —
# without it, bare "PN" matches inside "PNEUMATIC" (leading \b alone isn't
# enough: PNEUMATIC starts at a word boundary too).
PART_RE = re.compile(
    r"\b(?:P/N|PN|part\s*number|part\s*no\.?)(?![A-Za-z])\s*[:#.]?\s*([A-Z0-9][A-Z0-9\-/.]{1,19})",
    re.IGNORECASE,
)

# Quantities follow labels like "qty", "quantity", "each", "ea".
QTY_RE = re.compile(r"\b(?:qty|quantity|each|ea)\b\.?\s*[:#]?\s*(\d[\d,]*)", re.IGNORECASE)


def _unique(items: list[str]) -> list[str]:
    """Dedupe preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def extract_nsns(text: str) -> list[str]:
    """Extract National Stock Numbers (####-##-###-####) from text, deduped."""
    return _unique(NSN_RE.findall(text or ""))


def extract_part_numbers(text: str) -> list[str]:
    """Extract part numbers following P/N-style labels, deduped.

    Matches tokens after "P/N", "PN", "part number", or "part no" (optional
    separator). Trailing punctuation is stripped.
    """
    matches = [m.rstrip(".") for m in PART_RE.findall(text or "")]
    return _unique(matches)


def extract_quantities(text: str) -> list[int]:
    """Extract integer quantities following qty/quantity/each/ea labels.

    Comma-grouped numbers ("10,000") are supported. Results are deduped
    preserving first-seen order.
    """
    values = [int(m.replace(",", "")) for m in QTY_RE.findall(text or "")]
    seen: set[int] = set()
    out: list[int] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out
