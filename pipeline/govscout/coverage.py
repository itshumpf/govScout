"""Coverage ledger: one mechanism for both backfill and ongoing refresh.

A *slice* is one tracked PSC/FSC code crossed with one calendar month. The
*universe* is every slice implied by config (tracked_codes x lookback_months).
The *ledger* is a CSV file, committed to the repo, with one row per slice
tracking whether/when it was last fetched. ``CoverageLedger.select_next``
always picks never-fetched slices before stale ones (NULLS FIRST on
last_fetched), so the same selection function drives backfill (everything
starts null) and steady-state refresh (oldest last_fetched goes first) with
no mode switch — see README.md's "Coverage ledger" section for the full
rationale.
"""

from __future__ import annotations

import calendar
import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from .models import Solicitation

_LEDGER_FIELDS: tuple[str, ...] = (
    "code",
    "year",
    "month",
    "last_fetched",
    "status",
    "record_count",
    "last_error",
)


class QuotaExceeded(Exception):
    """A ``fetch_slice`` callable raises this to signal a clean stop.

    Distinct from a plain fetch failure: the caller should stop processing
    further slices immediately (don't burn remaining budget on requests that
    will also be rejected) but this is not itself an error to report loudly
    — see ``run_backfill``.
    """


@dataclass(frozen=True)
class Slice:
    """One tracked code x one calendar month."""

    code: str
    year: int
    month: int

    @property
    def key(self) -> str:
        return f"{self.code}:{self.year:04d}-{self.month:02d}"

    def posted_range(self, today: date | None = None) -> tuple[date, date]:
        """(postedFrom, postedTo) for this slice's calendar month.

        The end date is capped at ``today`` when the slice is the current,
        still-in-progress month, so we never ask SAM.gov for future-dated
        postings.
        """
        today = today or datetime.now(timezone.utc).date()
        start = date(self.year, self.month, 1)
        last_day = calendar.monthrange(self.year, self.month)[1]
        end = date(self.year, self.month, last_day)
        if end > today:
            end = today
        if start > end:
            end = start
        return start, end


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """Shift (year, month) by ``delta`` months (negative = backward)."""
    zero_based = (year * 12 + (month - 1)) + delta
    return zero_based // 12, zero_based % 12 + 1


def build_universe(
    tracked_codes: list[str],
    lookback_months: int,
    today: date | None = None,
) -> list[Slice]:
    """Every (code, month) slice implied by config.

    Covers ``lookback_months`` calendar months ending with the current
    month, inclusive, for each code in ``tracked_codes``. Config-driven by
    design — nothing here hardcodes which codes matter.
    """
    today = today or datetime.now(timezone.utc).date()
    months = [_shift_month(today.year, today.month, -offset) for offset in range(lookback_months)]
    months.reverse()  # oldest first
    return [Slice(code=code, year=y, month=m) for code in tracked_codes for (y, m) in months]


@dataclass
class LedgerRow:
    """One coverage ledger row — the persisted state of one slice."""

    code: str
    year: int
    month: int
    last_fetched: str | None = None
    status: str = "pending"
    record_count: int = 0
    last_error: str = ""

    @property
    def key(self) -> str:
        return f"{self.code}:{self.year:04d}-{self.month:02d}"


class CoverageLedger:
    """CSV-backed ledger of slice fetch state, one row per slice.

    Committed to the repo (not gitignored, unlike the scratch SQLite db) —
    this is state the data itself can't reconstruct: which slices have never
    been fetched at all.
    """

    def __init__(self, rows: dict[str, LedgerRow] | None = None) -> None:
        self.rows: dict[str, LedgerRow] = rows or {}

    @classmethod
    def load(cls, path: str | Path) -> CoverageLedger:
        """Load from CSV, or start empty if the file doesn't exist yet (first run)."""
        path = Path(path)
        if not path.exists():
            return cls()
        rows: dict[str, LedgerRow] = {}
        with path.open(newline="", encoding="utf-8") as fh:
            for raw in csv.DictReader(fh):
                row = LedgerRow(
                    code=raw["code"],
                    year=int(raw["year"]),
                    month=int(raw["month"]),
                    last_fetched=raw.get("last_fetched") or None,
                    status=raw.get("status") or "pending",
                    record_count=int(raw.get("record_count") or 0),
                    last_error=raw.get("last_error") or "",
                )
                rows[row.key] = row
        return cls(rows)

    def save(self, path: str | Path) -> Path:
        """Write the ledger back to CSV, sorted for stable, reviewable diffs."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=_LEDGER_FIELDS)
            writer.writeheader()
            for row in sorted(self.rows.values(), key=lambda r: (r.code, r.year, r.month)):
                writer.writerow(
                    {
                        "code": row.code,
                        "year": row.year,
                        "month": row.month,
                        "last_fetched": row.last_fetched or "",
                        "status": row.status,
                        "record_count": row.record_count,
                        "last_error": row.last_error,
                    }
                )
        return path

    def sync(self, universe: list[Slice]) -> None:
        """Add a pending row for any universe slice not already tracked.

        Additive only: a slice that falls out of the universe (code removed
        from config, or aged out of the lookback window) keeps its row
        rather than being deleted — history isn't discarded — but
        ``select_next`` won't pick it since it's filtered by the current
        universe.
        """
        for slc in universe:
            if slc.key not in self.rows:
                self.rows[slc.key] = LedgerRow(code=slc.code, year=slc.year, month=slc.month)

    def select_next(self, universe: list[Slice], n: int) -> list[LedgerRow]:
        """Next ``n`` slices to fetch: never-fetched first, then oldest last_fetched.

        This is the core of the design: sorting by (last_fetched is not
        None, last_fetched) puts every never-fetched row (None) ahead of
        every stale one, and orders stale ones oldest-first. Backfill (every
        row null) and steady-state refresh (every row stale by varying
        amounts) are the same sort with no mode switch.
        """
        universe_keys = {s.key for s in universe}
        candidates = [r for r in self.rows.values() if r.key in universe_keys]
        candidates.sort(key=lambda r: (r.last_fetched is not None, r.last_fetched or "", r.key))
        return candidates[: max(n, 0)]

    def mark_success(self, key: str, when: str, record_count: int) -> None:
        """Advance a slice only after its records are written — see run_backfill."""
        row = self.rows[key]
        row.last_fetched = when
        row.status = "success"
        row.record_count = record_count
        row.last_error = ""

    def mark_error(self, key: str, message: str) -> None:
        """Record a failure WITHOUT advancing last_fetched.

        Leaving last_fetched untouched (null stays null, stale stays stale)
        is what makes a failed slice get picked again next run instead of
        silently falling behind forever.
        """
        row = self.rows[key]
        row.status = "error"
        row.last_error = message[:500]


@dataclass
class BackfillResult:
    requested: int
    completed: int
    failed: int
    stopped_reason: str | None  # "quota" or None


def run_backfill(
    ledger: CoverageLedger,
    universe: list[Slice],
    n: int,
    fetch_slice: Callable[[Slice], list[dict]],
    normalize: Callable[[dict], Solicitation],
    now: datetime | None = None,
) -> tuple[BackfillResult, dict[str, list[Solicitation]]]:
    """Process up to ``n`` slices: fetch, normalize, advance ledger on success only.

    ``fetch_slice`` should raise ``QuotaExceeded`` to signal a clean stop
    (budget exhausted — not an error) and any other exception for a
    per-slice failure (network, bad status, etc.) that should be logged and
    skipped, retrying that slice next run rather than blocking the rest of
    this one.

    Returns the run summary plus a ``{slice_key: [Solicitation, ...]}`` map
    of only the slices that succeeded this run — the caller uses that to
    replace exactly those slices' records in the dashboard export.
    """
    now = now or datetime.now(timezone.utc)
    ledger.sync(universe)
    selected = ledger.select_next(universe, n)

    sols_by_slice: dict[str, list[Solicitation]] = {}
    completed = failed = 0
    stopped_reason: str | None = None

    for row in selected:
        slc = Slice(row.code, row.year, row.month)
        try:
            raw = fetch_slice(slc)
            sols = [normalize(record) for record in raw]
        except QuotaExceeded as exc:
            ledger.mark_error(slc.key, str(exc))
            stopped_reason = "quota"
            break
        except Exception as exc:  # noqa: BLE001 — any fetch/normalize failure: log, retry next run
            ledger.mark_error(slc.key, str(exc))
            failed += 1
            continue

        sols_by_slice[slc.key] = sols
        ledger.mark_success(slc.key, now.isoformat(timespec="seconds"), len(sols))
        completed += 1

    result = BackfillResult(
        requested=len(selected),
        completed=completed,
        failed=failed,
        stopped_reason=stopped_reason,
    )
    return result, sols_by_slice
