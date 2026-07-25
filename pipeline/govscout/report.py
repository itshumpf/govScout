"""Reporting: CSV/JSON export and console digest rendering."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from .models import ROW_FIELDS, Solicitation

TIER_HIGH = 70
TIER_MEDIUM = 40
_TOP_PER_TIER = 5


def _tier(score: int) -> str:
    if score >= TIER_HIGH:
        return "High"
    if score >= TIER_MEDIUM:
        return "Medium"
    return "Low"


def export_csv(sols: list[Solicitation], path: str | Path) -> Path:
    """Write solicitations to CSV.

    Columns come from ``Solicitation.to_row()`` (ROW_FIELDS order); list
    fields are already joined with "; ". Parent directories are created.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(ROW_FIELDS))
        writer.writeheader()
        for sol in sols:
            writer.writerow(sol.to_row())
    return path


def export_json(
    sols: list[Solicitation],
    path: str | Path,
    source: str,
    stored: int,
) -> Path:
    """Write a dashboard-ready JSON export.

    Layout::

        {
          "generated_at": "<ISO timestamp>",
          "source": "demo|sam.gov",
          "stats": {"fetched", "stored", "with_pricing_signals",
                    "high", "medium", "low"},
          "solicitations": [ { ...to_row() fields...,
                               "pricing_flags": ["..."] } ]
        }

    Solicitations are sorted by pricing_score descending; tiers follow the
    digest thresholds (high >= 70, medium 40-69, low < 40). ``stored`` is the
    total row count in the local database after the upsert. Parent
    directories are created.
    """
    ranked = sorted(sols, key=lambda s: s.pricing_score, reverse=True)
    tiers = {"high": 0, "medium": 0, "low": 0}
    for sol in sols:
        tiers[_tier(sol.pricing_score).lower()] += 1
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "stats": {
            "fetched": len(sols),
            "stored": stored,
            "with_pricing_signals": sum(1 for s in sols if s.pricing_flags),
            **tiers,
        },
        "solicitations": [
            {**sol.to_row(), "pricing_flags": list(sol.pricing_flags)}
            for sol in ranked
        ],
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _one_liner(sol: Solicitation) -> str:
    parts = [f"[{sol.pricing_score:>3}] {sol.sol_number} — {sol.title} ({sol.agency})"]
    if sol.nsns:
        parts.append(f"NSN: {'; '.join(sol.nsns)}")
    if sol.part_numbers:
        parts.append(f"P/N: {'; '.join(sol.part_numbers)}")
    if sol.quantities:
        parts.append(f"qty: {'; '.join(str(q) for q in sol.quantities)}")
    return "  ".join(parts)


def render_digest(sols: list[Solicitation]) -> str:
    """Render a console digest grouped by pricing-score tier.

    Tiers: High (>=70) / Medium (40-69) / Low (<40). Header reports total
    count and how many have any pricing signal. Top items per tier are
    shown as one-liners.
    """
    n = len(sols)
    with_signals = sum(1 for s in sols if s.pricing_flags)
    lines = [
        "=" * 64,
        f"GovScout digest: {n} new solicitations, {with_signals} with pricing signals",
        "=" * 64,
    ]
    if not sols:
        lines.append("No solicitations found.")
        return "\n".join(lines)

    ranked = sorted(sols, key=lambda s: s.pricing_score, reverse=True)
    for tier in ("High", "Medium", "Low"):
        group = [s for s in ranked if _tier(s.pricing_score) == tier]
        if not group:
            continue
        bounds = (
            f">= {TIER_HIGH}"
            if tier == "High"
            else f"{TIER_MEDIUM}-{TIER_HIGH - 1}"
            if tier == "Medium"
            else f"< {TIER_MEDIUM}"
        )
        lines.append("")
        lines.append(f"{tier.upper()} priority (score {bounds}) — {len(group)}")
        lines.append("-" * 64)
        for sol in group[:_TOP_PER_TIER]:
            lines.append("  " + _one_liner(sol))
        if len(group) > _TOP_PER_TIER:
            lines.append(f"  ... and {len(group) - _TOP_PER_TIER} more")
    return "\n".join(lines)
