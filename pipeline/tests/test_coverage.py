"""Tests for the coverage ledger: universe generation, selection ordering,
advance-only-on-success, and quota stop (offline, no network)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from govscout.coverage import (
    CoverageLedger,
    LedgerRow,
    QuotaExceeded,
    Slice,
    build_universe,
    run_backfill,
)
from govscout.models import Solicitation


def _raw(sol_number: str) -> dict:
    return {
        "sol_number": sol_number,
        "title": "Widget",
        "agency": "DLA Aviation",
        "psc_code": "5340",
        "posted_date": "2026-07-15",
        "response_deadline": None,
        "description": "",
        "url": None,
        "attachments": [],
    }


def _normalize(raw: dict) -> Solicitation:
    return Solicitation(
        sol_number=raw["sol_number"],
        title=raw["title"],
        agency=raw["agency"],
        psc_code=raw["psc_code"],
        posted_date=raw["posted_date"],
        response_deadline=raw["response_deadline"],
        description=raw["description"],
        url=raw["url"],
        attachments=[],
        nsns=[],
        part_numbers=[],
        quantities=[],
        pricing_score=0,
        pricing_flags=[],
        fetched_at="2026-07-26T00:00:00+00:00",
    )


class TestSlice:
    def test_key_format(self):
        assert Slice("5340", 2026, 3).key == "5340:2026-03"

    def test_posted_range_full_past_month(self):
        slc = Slice("5340", 2026, 3)
        start, end = slc.posted_range(today=date(2026, 7, 1))
        assert (start, end) == (date(2026, 3, 1), date(2026, 3, 31))

    def test_posted_range_caps_current_month_at_today(self):
        slc = Slice("5340", 2026, 7)
        start, end = slc.posted_range(today=date(2026, 7, 15))
        assert (start, end) == (date(2026, 7, 1), date(2026, 7, 15))


class TestBuildUniverse:
    def test_one_slice_per_code_per_month(self):
        universe = build_universe(["5340", "5962"], lookback_months=3, today=date(2026, 7, 15))
        assert len(universe) == 6

    def test_months_are_the_trailing_window_inclusive_of_current(self):
        universe = build_universe(["5340"], lookback_months=3, today=date(2026, 1, 15))
        months = [(s.year, s.month) for s in universe]
        # Rolls back across the year boundary correctly.
        assert months == [(2025, 11), (2025, 12), (2026, 1)]

    def test_no_hardcoded_codes_reflects_whatever_config_passes_in(self):
        universe = build_universe(["9999"], lookback_months=1, today=date(2026, 7, 15))
        assert universe == [Slice("9999", 2026, 7)]


class TestSelectNextNullsFirst:
    """The core ordering requirement: never-fetched slices sort ahead of stale ones."""

    def test_never_fetched_sorts_before_stale(self):
        universe = [Slice("A", 2026, 1), Slice("B", 2026, 1)]
        ledger = CoverageLedger(
            {
                "A:2026-01": LedgerRow("A", 2026, 1, last_fetched="2026-01-01T00:00:00+00:00", status="success"),
                "B:2026-01": LedgerRow("B", 2026, 1, last_fetched=None, status="pending"),
            }
        )
        selected = ledger.select_next(universe, n=2)
        assert [r.code for r in selected] == ["B", "A"]

    def test_among_stale_rows_oldest_last_fetched_goes_first(self):
        universe = [Slice("A", 2026, 1), Slice("B", 2026, 1), Slice("C", 2026, 1)]
        ledger = CoverageLedger(
            {
                "A:2026-01": LedgerRow("A", 2026, 1, last_fetched="2026-01-03T00:00:00+00:00", status="success"),
                "B:2026-01": LedgerRow("B", 2026, 1, last_fetched="2026-01-01T00:00:00+00:00", status="success"),
                "C:2026-01": LedgerRow("C", 2026, 1, last_fetched="2026-01-02T00:00:00+00:00", status="success"),
            }
        )
        selected = ledger.select_next(universe, n=3)
        assert [r.code for r in selected] == ["B", "C", "A"]

    def test_takes_next_n_only(self):
        universe = [Slice(str(i), 2026, 1) for i in range(5)]
        ledger = CoverageLedger()
        ledger.sync(universe)
        assert len(ledger.select_next(universe, n=2)) == 2

    def test_backfill_and_refresh_are_the_same_call_no_mode_switch(self):
        # All-null universe (pure backfill) and all-stale universe (pure
        # refresh) both just fall out of the same select_next sort.
        universe = [Slice("A", 2026, 1), Slice("B", 2026, 1)]
        backfill_ledger = CoverageLedger()
        backfill_ledger.sync(universe)
        assert {r.code for r in backfill_ledger.select_next(universe, n=2)} == {"A", "B"}

        refresh_ledger = CoverageLedger(
            {
                "A:2026-01": LedgerRow("A", 2026, 1, last_fetched="2026-01-01T00:00:00+00:00", status="success"),
                "B:2026-01": LedgerRow("B", 2026, 1, last_fetched="2026-01-02T00:00:00+00:00", status="success"),
            }
        )
        assert [r.code for r in refresh_ledger.select_next(universe, n=1)] == ["A"]

    def test_selection_filtered_to_current_universe_only(self):
        # A ledger row for a slice no longer in the universe (code dropped
        # from config, or aged out of lookback) is never selected, but stays
        # in the ledger (sync() is additive-only, never deletes).
        universe = [Slice("A", 2026, 1)]
        ledger = CoverageLedger({"ORPHAN:2020-01": LedgerRow("ORPHAN", 2020, 1)})
        ledger.sync(universe)
        selected = ledger.select_next(universe, n=10)
        assert [r.key for r in selected] == ["A:2026-01"]
        assert "ORPHAN:2020-01" in ledger.rows  # not deleted


class TestRunBackfillNoAdvanceOnFailure:
    def test_failed_slice_ledger_row_left_null(self):
        universe = [Slice("A", 2026, 1)]
        ledger = CoverageLedger()

        def failing_fetch(slc: Slice) -> list[dict]:
            raise RuntimeError("network blip")

        result, sols_by_slice = run_backfill(ledger, universe, n=1, fetch_slice=failing_fetch, normalize=_normalize)

        assert result.completed == 0
        assert result.failed == 1
        assert sols_by_slice == {}
        row = ledger.rows["A:2026-01"]
        assert row.last_fetched is None  # never advanced
        assert row.status == "error"
        assert "network blip" in row.last_error

    def test_failed_slice_is_selected_again_next_run(self):
        universe = [Slice("A", 2026, 1), Slice("B", 2026, 1)]
        ledger = CoverageLedger()

        def fail_a_succeed_b(slc: Slice) -> list[dict]:
            if slc.code == "A":
                raise RuntimeError("boom")
            return [_raw("SOL-B")]

        run_backfill(ledger, universe, n=2, fetch_slice=fail_a_succeed_b, normalize=_normalize)
        # B succeeded and advanced; A is still null and sorts first next time.
        selected = ledger.select_next(universe, n=1)
        assert selected[0].code == "A"

    def test_success_advances_last_fetched_and_record_count(self):
        universe = [Slice("A", 2026, 1)]
        ledger = CoverageLedger()
        now = datetime(2026, 7, 26, tzinfo=timezone.utc)

        result, sols_by_slice = run_backfill(
            ledger,
            universe,
            n=1,
            fetch_slice=lambda slc: [_raw("SOL-1"), _raw("SOL-2")],
            normalize=_normalize,
            now=now,
        )

        assert result.completed == 1
        row = ledger.rows["A:2026-01"]
        assert row.status == "success"
        assert row.record_count == 2
        assert row.last_fetched == now.isoformat(timespec="seconds")
        assert len(sols_by_slice["A:2026-01"]) == 2


class TestRunBackfillIdempotent:
    def test_rerunning_same_slice_is_a_noop_on_record_count(self):
        universe = [Slice("A", 2026, 1)]
        ledger = CoverageLedger()

        def fetch_slice(slc: Slice) -> list[dict]:
            return [_raw("SOL-1"), _raw("SOL-2")]

        # Run twice, each time as a fresh "select next 1" — since after the
        # first run last_fetched is set, a second run only re-selects A if
        # nothing else is pending (simulate directly rather than via select).
        result1, sols1 = run_backfill(ledger, universe, n=1, fetch_slice=fetch_slice, normalize=_normalize)
        # Force reselection by clearing last_fetched is not the point here —
        # what matters is that calling the fetch+replace step twice for the
        # same slice produces the same record count, not an accumulation.
        ledger.rows["A:2026-01"].last_fetched = None
        result2, sols2 = run_backfill(ledger, universe, n=1, fetch_slice=fetch_slice, normalize=_normalize)

        assert len(sols1["A:2026-01"]) == len(sols2["A:2026-01"]) == 2
        assert ledger.rows["A:2026-01"].record_count == 2


class TestRunBackfillQuotaStop:
    def test_stops_cleanly_without_erroring_remaining_slices(self):
        universe = [Slice("A", 2026, 1), Slice("B", 2026, 1), Slice("C", 2026, 1)]
        ledger = CoverageLedger()
        calls: list[str] = []

        def fetch_slice(slc: Slice) -> list[dict]:
            calls.append(slc.code)
            if slc.code == "B":
                raise QuotaExceeded("HTTP 429")
            return [_raw(f"SOL-{slc.code}")]

        result, sols_by_slice = run_backfill(ledger, universe, n=3, fetch_slice=fetch_slice, normalize=_normalize)

        assert calls == ["A", "B"]  # never attempted C — didn't burn the rest of the budget
        assert result.completed == 1  # only A
        assert result.stopped_reason == "quota"
        assert "A:2026-01" in sols_by_slice
        assert "B:2026-01" not in sols_by_slice
        assert "C:2026-01" not in sols_by_slice

    def test_quota_stopped_slice_not_advanced_and_retried_next_run(self):
        universe = [Slice("A", 2026, 1)]
        ledger = CoverageLedger()

        def always_quota(slc: Slice) -> list[dict]:
            raise QuotaExceeded("HTTP 429")

        run_backfill(ledger, universe, n=1, fetch_slice=always_quota, normalize=_normalize)
        row = ledger.rows["A:2026-01"]
        assert row.last_fetched is None
        selected = ledger.select_next(universe, n=1)
        assert selected[0].code == "A"


class TestCoverageLedgerCsvRoundtrip:
    def test_save_and_load_roundtrip(self, tmp_path):
        path = tmp_path / "coverage_ledger.csv"
        ledger = CoverageLedger(
            {
                "5340:2026-01": LedgerRow(
                    "5340", 2026, 1, last_fetched="2026-01-05T00:00:00+00:00", status="success", record_count=12
                ),
                "5962:2026-02": LedgerRow("5962", 2026, 2, status="error", last_error="HTTP 401"),
            }
        )
        ledger.save(path)
        loaded = CoverageLedger.load(path)

        assert loaded.rows["5340:2026-01"].record_count == 12
        assert loaded.rows["5340:2026-01"].last_fetched == "2026-01-05T00:00:00+00:00"
        assert loaded.rows["5962:2026-02"].last_fetched is None
        assert loaded.rows["5962:2026-02"].last_error == "HTTP 401"

    def test_missing_file_loads_empty(self, tmp_path):
        ledger = CoverageLedger.load(tmp_path / "does-not-exist.csv")
        assert ledger.rows == {}

    def test_sync_adds_missing_slices_as_pending_without_touching_existing(self, tmp_path):
        universe = [Slice("A", 2026, 1), Slice("B", 2026, 1)]
        ledger = CoverageLedger({"A:2026-01": LedgerRow("A", 2026, 1, status="success", last_fetched="x")})
        ledger.sync(universe)
        assert ledger.rows["A:2026-01"].status == "success"  # untouched
        assert ledger.rows["B:2026-01"].status == "pending"  # newly added
