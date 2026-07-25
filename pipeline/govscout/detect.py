"""Pricing-signal detection heuristics.

Scores solicitation text (plus attachment names) for signals that an
opportunity has actionable pricing — the core filter a DLA/DIBBS-style
quoting workflow cares about. Pure functions; no I/O.
"""

from __future__ import annotations

import re

# Keyword families: flag -> (weight, keywords). Matching is case-insensitive
# and word-boundary anchored so "rfq" does not match inside other tokens.
_FAMILIES: dict[str, tuple[int, tuple[str, ...]]] = {
    "online_pricing": (40, ("online pricing", "enter your quote", "automated evaluation")),
    "historical_pricing": (25, ("historical pricing", "price history", "prior award", "last paid")),
    "quote_requested": (20, ("request for quotation", "rfq", "quote due")),
    "competitive": (10, ("competitive", "full and open")),
}

# Bonus when an attachment filename itself suggests pricing content.
_ATTACHMENT_BONUS = 15
_ATTACHMENT_RE = re.compile(r"pric(e|ing)|quote|cost", re.IGNORECASE)

MAX_SCORE = 100


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    """Return True if any keyword appears in text (case-insensitive, word-boundary)."""
    return any(re.search(r"\b" + re.escape(keyword) + r"\b", text, re.IGNORECASE) for keyword in keywords)


def score_pricing(sol_text: str, attachments: list[str]) -> tuple[int, list[str]]:
    """Score a solicitation for pricing-availability signals.

    Args:
        sol_text: Combined solicitation text (title + description, etc.).
        attachments: Attachment filenames or URLs.

    Returns:
        (score, flags) where score is 0-100 (weighted additive per matched
        keyword family, plus an attachment bonus, capped at 100) and flags is
        the list of matched family names in stable order.
    """
    text = sol_text or ""
    score = 0
    flags: list[str] = []
    for flag, (weight, keywords) in _FAMILIES.items():
        if _contains_any(text, keywords):
            score += weight
            flags.append(flag)
    if any(_ATTACHMENT_RE.search(name or "") for name in attachments):
        score += _ATTACHMENT_BONUS
        flags.append("pricing_attachment")
    return min(score, MAX_SCORE), flags
