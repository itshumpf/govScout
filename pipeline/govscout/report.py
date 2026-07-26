"""Reporting: CSV/JSON export and console digest rendering."""

from __future__ import annotations

import csv
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .models import ROW_FIELDS, Solicitation

TIER_HIGH = 70
TIER_MEDIUM = 40
_TOP_PER_TIER = 5
FRESHNESS_MAX_AGE_DAYS = 45  # drop no-deadline rows older than this by posted_date


def _utc_today() -> date:
    """Current UTC date. A separate function so tests can monkeypatch "today"."""
    return datetime.now(timezone.utc).date()


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


def merge_solicitation_rows(existing: list[dict], new_sols: list[Solicitation]) -> list[dict]:
    """Merge freshly-fetched solicitations into existing dashboard-row dicts.

    Deduped by ``sol_number``; when an id appears in both, the row with the
    lexicographically greatest ``fetched_at`` (ISO-8601, always UTC in this
    codebase, so this sorts correctly) wins. Existing rows are dicts shaped
    like ``export_json``'s ``solicitations`` entries (``to_row()`` fields
    plus a list-valued ``pricing_flags``).
    """
    by_id: dict[str, dict] = {row["sol_number"]: row for row in existing if row.get("sol_number")}
    for sol in new_sols:
        row = {**sol.to_row(), "pricing_flags": list(sol.pricing_flags)}
        current = by_id.get(sol.sol_number)
        if current is None or row.get("fetched_at", "") >= current.get("fetched_at", ""):
            by_id[sol.sol_number] = row
    return list(by_id.values())


def apply_freshness(
    rows: list[dict],
    today: date | None = None,
    max_age_days: int = FRESHNESS_MAX_AGE_DAYS,
) -> list[dict]:
    """Drop stale dashboard rows so the board stays current and bounded.

    A row is dropped if its ``response_deadline`` has passed (before
    ``today``), or — when there is no deadline — if its ``posted_date`` is
    older than ``max_age_days``. Rows with unparseable/missing dates are
    kept rather than guessed away.
    """
    today = today or _utc_today()
    cutoff = today - timedelta(days=max_age_days)
    kept: list[dict] = []
    for row in rows:
        deadline = row.get("response_deadline")
        if deadline:
            try:
                if date.fromisoformat(deadline) < today:
                    continue
            except ValueError:
                pass
        else:
            posted = row.get("posted_date")
            if posted:
                try:
                    if date.fromisoformat(posted) < cutoff:
                        continue
                except ValueError:
                    pass
        kept.append(row)
    return kept


def export_json_accumulated(
    new_sols: list[Solicitation],
    path: str | Path,
    source: str,
    today: date | None = None,
    max_age_days: int = FRESHNESS_MAX_AGE_DAYS,
) -> Path:
    """Merge ``new_sols`` into the dashboard JSON already at ``path``.

    Unlike :func:`export_json` (which writes a snapshot of just what was
    passed in), this reads any existing file at ``path``, merges in today's
    fetch (dedup by sol_number, newest ``fetched_at`` wins — see
    :func:`merge_solicitation_rows`), drops stale rows (see
    :func:`apply_freshness`), and recomputes ``stats`` over that merged and
    cleaned set. ``stats.fetched`` is this run's raw fetch count; every
    other stat describes the full merged+cleaned board. Designed for the
    weekly PSC rotation: a small daily fetch accumulates into a full,
    always-current board over the week instead of being overwritten by it.

    A missing or unreadable existing file is treated as an empty board
    (first run / corrupt file), not an error.
    """
    path = Path(path)
    existing_rows: list[dict] = []
    if path.exists():
        try:
            existing_payload = json.loads(path.read_text(encoding="utf-8"))
            existing_rows = list(existing_payload.get("solicitations") or [])
        except (json.JSONDecodeError, OSError):
            existing_rows = []

    merged = merge_solicitation_rows(existing_rows, new_sols)
    cleaned = apply_freshness(merged, today=today, max_age_days=max_age_days)
    ranked = sorted(cleaned, key=lambda row: row.get("pricing_score", 0), reverse=True)

    tiers = {"high": 0, "medium": 0, "low": 0}
    for row in cleaned:
        tiers[_tier(int(row.get("pricing_score") or 0)).lower()] += 1

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "stats": {
            "fetched": len(new_sols),
            "stored": len(cleaned),
            "with_pricing_signals": sum(1 for row in cleaned if row.get("pricing_flags")),
            **tiers,
        },
        "solicitations": ranked,
    }
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
