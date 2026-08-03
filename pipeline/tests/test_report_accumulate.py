"""Tests for the accumulate + freshness dashboard-merge logic (offline, no network)."""

from __future__ import annotations

import json
from datetime import date

from govscout.models import Solicitation
from govscout.report import (
    apply_freshness,
    export_json_accumulated,
    export_json_by_slice,
    merge_solicitation_rows,
)

_TODAY = date(2026, 7, 26)


def _sol(
    sol_number: str,
    fetched_at: str,
    response_deadline: str | None = None,
    posted_date: str = "2026-07-01",
    pricing_score: int = 50,
    title: str = "Widget RFQ",
) -> Solicitation:
    return Solicitation(
        sol_number=sol_number,
        title=title,
        agency="DLA Aviation",
        psc_code="5340",
        posted_date=posted_date,
        response_deadline=response_deadline,
        description="",
        url=None,
        attachments=[],
        nsns=[],
        part_numbers=[],
        quantities=[],
        pricing_score=pricing_score,
        pricing_flags=[],
        fetched_at=fetched_at,
    )


def _row(**overrides) -> dict:
    base = {
        "sol_number": "SOL-1",
        "title": "old title",
        "agency": "DLA",
        "psc_code": "5340",
        "posted_date": "2026-07-01",
        "response_deadline": "",
        "url": "",
        "attachments": "",
        "nsns": "",
        "part_numbers": "",
        "quantities": "",
        "pricing_score": 10,
        "pricing_flags": [],
        "description": "",
        "fetched_at": "2026-07-20T00:00:00+00:00",
    }
    base.update(overrides)
    return base


class TestMergeSolicitationRows:
    def test_new_id_is_added_alongside_existing(self):
        existing = [_row(sol_number="SOL-OLD")]
        new = [_sol("SOL-NEW", fetched_at="2026-07-26T00:00:00+00:00")]
        merged = merge_solicitation_rows(existing, new)
        assert {row["sol_number"] for row in merged} == {"SOL-OLD", "SOL-NEW"}

    def test_newer_fetched_at_wins_on_id_collision(self):
        existing = [_row(sol_number="SOL-1", title="stale", fetched_at="2026-07-01T00:00:00+00:00")]
        new = [_sol("SOL-1", fetched_at="2026-07-26T00:00:00+00:00", title="fresh")]
        merged = merge_solicitation_rows(existing, new)
        assert len(merged) == 1
        assert merged[0]["title"] == "fresh"

    def test_older_incoming_fetch_does_not_overwrite_newer_existing(self):
        existing = [_row(sol_number="SOL-1", title="newer", fetched_at="2026-07-26T00:00:00+00:00")]
        new = [_sol("SOL-1", fetched_at="2020-01-01T00:00:00+00:00", title="stale-replay")]
        merged = merge_solicitation_rows(existing, new)
        assert merged[0]["title"] == "newer"

    def test_empty_existing_behaves_like_a_fresh_export(self):
        new = [_sol("A", fetched_at="2026-07-26T00:00:00+00:00"), _sol("B", fetched_at="2026-07-26T00:00:00+00:00")]
        merged = merge_solicitation_rows([], new)
        assert {row["sol_number"] for row in merged} == {"A", "B"}


class TestApplyFreshness:
    def test_drops_row_with_passed_deadline(self):
        rows = [_row(sol_number="EXPIRED", response_deadline="2026-07-01")]
        kept = apply_freshness(rows, today=_TODAY)
        assert kept == []

    def test_keeps_row_with_future_deadline(self):
        rows = [_row(sol_number="OPEN", response_deadline="2026-08-01")]
        kept = apply_freshness(rows, today=_TODAY)
        assert [r["sol_number"] for r in kept] == ["OPEN"]

    def test_keeps_row_with_deadline_equal_to_today(self):
        rows = [_row(sol_number="DUE-TODAY", response_deadline=_TODAY.isoformat())]
        kept = apply_freshness(rows, today=_TODAY)
        assert [r["sol_number"] for r in kept] == ["DUE-TODAY"]

    def test_drops_no_deadline_row_older_than_max_age(self):
        rows = [_row(sol_number="STALE", response_deadline="", posted_date="2026-05-01")]
        kept = apply_freshness(rows, today=_TODAY, max_age_days=45)
        assert kept == []

    def test_keeps_no_deadline_row_within_max_age(self):
        rows = [_row(sol_number="RECENT", response_deadline="", posted_date="2026-07-20")]
        kept = apply_freshness(rows, today=_TODAY, max_age_days=45)
        assert [r["sol_number"] for r in kept] == ["RECENT"]

    def test_keeps_row_with_unparseable_dates_rather_than_dropping(self):
        rows = [_row(sol_number="WEIRD", response_deadline="not-a-date")]
        kept = apply_freshness(rows, today=_TODAY)
        assert [r["sol_number"] for r in kept] == ["WEIRD"]


class TestExportJsonAccumulated:
    def test_first_run_creates_file_like_a_normal_export(self, tmp_path):
        path = tmp_path / "dashboard.json"
        new = [_sol("SOL-1", fetched_at="2026-07-26T00:00:00+00:00", response_deadline="2026-08-01")]
        export_json_accumulated(new, path, source="sam.gov", today=_TODAY)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert [s["sol_number"] for s in data["solicitations"]] == ["SOL-1"]
        assert data["stats"]["fetched"] == 1
        assert data["stats"]["stored"] == 1

    def test_second_run_with_different_group_accumulates(self, tmp_path):
        path = tmp_path / "dashboard.json"
        monday = [_sol("MON-1", fetched_at="2026-07-20T00:00:00+00:00", response_deadline="2026-08-01")]
        export_json_accumulated(monday, path, source="sam.gov", today=date(2026, 7, 20))

        tuesday = [_sol("TUE-1", fetched_at="2026-07-21T00:00:00+00:00", response_deadline="2026-08-01")]
        export_json_accumulated(tuesday, path, source="sam.gov", today=date(2026, 7, 21))

        data = json.loads(path.read_text(encoding="utf-8"))
        ids = {s["sol_number"] for s in data["solicitations"]}
        assert ids == {"MON-1", "TUE-1"}
        assert data["stats"]["fetched"] == 1  # only today's run, not the accumulated total
        assert data["stats"]["stored"] == 2

    def test_stale_rows_drop_out_on_next_run_even_without_reappearing(self, tmp_path):
        path = tmp_path / "dashboard.json"
        expiring = [_sol("EXPIRING", fetched_at="2026-07-01T00:00:00+00:00", response_deadline="2026-07-10")]
        export_json_accumulated(expiring, path, source="sam.gov", today=date(2026, 7, 1))

        # A later run (different day/group) that doesn't even mention EXPIRING
        # should still see it dropped by the freshness pass.
        later = [_sol("NEW", fetched_at="2026-07-26T00:00:00+00:00", response_deadline="2026-08-01")]
        export_json_accumulated(later, path, source="sam.gov", today=_TODAY)

        data = json.loads(path.read_text(encoding="utf-8"))
        ids = {s["sol_number"] for s in data["solicitations"]}
        assert ids == {"NEW"}

    def test_corrupt_existing_file_is_treated_as_empty_not_fatal(self, tmp_path):
        path = tmp_path / "dashboard.json"
        path.write_text("{not valid json", encoding="utf-8")
        new = [_sol("SOL-1", fetched_at="2026-07-26T00:00:00+00:00", response_deadline="2026-08-01")]
        export_json_accumulated(new, path, source="sam.gov", today=_TODAY)  # must not raise
        data = json.loads(path.read_text(encoding="utf-8"))
        assert [s["sol_number"] for s in data["solicitations"]] == ["SOL-1"]

    def test_stats_recomputed_over_merged_and_cleaned_set(self, tmp_path):
        path = tmp_path / "dashboard.json"
        first = [
            _sol("HIGH-1", fetched_at="2026-07-20T00:00:00+00:00", response_deadline="2026-08-01", pricing_score=90),
        ]
        export_json_accumulated(first, path, source="sam.gov", today=date(2026, 7, 20))

        second = [
            _sol("LOW-1", fetched_at="2026-07-26T00:00:00+00:00", response_deadline="2026-08-01", pricing_score=10),
        ]
        export_json_accumulated(second, path, source="sam.gov", today=_TODAY)

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["stats"]["high"] == 1
        assert data["stats"]["low"] == 1
        assert data["stats"]["stored"] == 2
        # Ranked descending by pricing_score across the whole merged set.
        assert [s["sol_number"] for s in data["solicitations"]] == ["HIGH-1", "LOW-1"]


class TestDescriptionEnrichedStat:
    """stats.description_enriched — see describe.py; the board-visible signal
    that some records may still hold a SAM.gov link instead of real text."""

    def test_counts_only_records_with_real_text(self, tmp_path):
        path = tmp_path / "dashboard.json"
        enriched = _sol("REAL-1", fetched_at="2026-07-26T00:00:00+00:00", response_deadline="2026-08-01")
        enriched.description = "Real narrative text, RFQ due 8/1"
        enriched.description_enriched = True

        not_yet = _sol("LINK-1", fetched_at="2026-07-26T00:00:00+00:00", response_deadline="2026-08-01")
        not_yet.description = "https://api.sam.gov/prod/opportunities/v1/noticedesc?noticeid=x"
        not_yet.description_enriched = False

        export_json_accumulated([enriched, not_yet], path, source="sam.gov", today=_TODAY)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["stats"]["stored"] == 2
        assert data["stats"]["description_enriched"] == 1

    def test_export_json_by_slice_counts_correctly_too(self, tmp_path):
        path = tmp_path / "dashboard.json"
        enriched = _sol("REAL-1", fetched_at="2026-01-05T00:00:00+00:00", response_deadline="2026-08-01")
        enriched.description_enriched = True
        sols = {"5340:2026-01": [enriched]}
        export_json_by_slice(sols, path, source="sam.gov", today=_TODAY)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["stats"]["description_enriched"] == 1

    def test_pre_migration_rows_without_the_field_count_as_not_enriched(self, tmp_path):
        """A dashboard.json written before description_enriched existed has no
        such key on its rows at all — must not error, must count as 0."""
        path = tmp_path / "dashboard.json"
        path.write_text(
            json.dumps(
                {
                    "generated_at": "2026-01-01T00:00:00+00:00",
                    "source": "sam.gov",
                    "stats": {},
                    "solicitations": [_row(sol_number="LEGACY-1", response_deadline="2026-08-01")],
                }
            ),
            encoding="utf-8",
        )
        new = [_sol("SOL-1", fetched_at="2026-07-26T00:00:00+00:00", response_deadline="2026-08-01")]
        export_json_accumulated(new, path, source="sam.gov", today=_TODAY)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["stats"]["stored"] == 2
        assert data["stats"]["description_enriched"] == 0


class TestExportJsonBySlice:
    """Idempotent replace-by-slice — the coverage ledger's export path."""

    def test_first_run_writes_rows_tagged_with_slice_key(self, tmp_path):
        path = tmp_path / "dashboard.json"
        sols = {"5340:2026-01": [_sol("SOL-1", fetched_at="2026-01-05T00:00:00+00:00", response_deadline="2026-08-01")]}
        export_json_by_slice(sols, path, source="sam.gov", today=_TODAY)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["solicitations"][0]["slice_key"] == "5340:2026-01"
        assert data["stats"]["fetched"] == 1
        assert data["stats"]["stored"] == 1

    def test_rerunning_same_slice_is_a_noop_on_record_count(self, tmp_path):
        path = tmp_path / "dashboard.json"
        sols = {
            "5340:2026-01": [
                _sol("SOL-1", fetched_at="2026-01-05T00:00:00+00:00", response_deadline="2026-08-01"),
                _sol("SOL-2", fetched_at="2026-01-05T00:00:00+00:00", response_deadline="2026-08-01"),
            ]
        }
        export_json_by_slice(sols, path, source="sam.gov", today=_TODAY)
        export_json_by_slice(sols, path, source="sam.gov", today=_TODAY)  # identical re-fetch
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["stats"]["stored"] == 2  # not 4 — replaced, not appended

    def test_record_missing_from_refetch_is_dropped_not_orphaned(self, tmp_path):
        path = tmp_path / "dashboard.json"
        first = {
            "5340:2026-01": [
                _sol("SOL-1", fetched_at="2026-01-05T00:00:00+00:00", response_deadline="2026-08-01"),
                _sol("SOL-2", fetched_at="2026-01-05T00:00:00+00:00", response_deadline="2026-08-01"),
            ]
        }
        export_json_by_slice(first, path, source="sam.gov", today=_TODAY)

        # SOL-2 vanished from SAM.gov (cancelled/awarded) — the re-fetch of
        # the same slice only returns SOL-1. Unlike merge-by-sol_number,
        # SOL-2 must not linger just because it didn't reappear.
        second = {"5340:2026-01": [_sol("SOL-1", fetched_at="2026-02-01T00:00:00+00:00", response_deadline="2026-08-01")]}
        export_json_by_slice(second, path, source="sam.gov", today=_TODAY)

        data = json.loads(path.read_text(encoding="utf-8"))
        ids = {s["sol_number"] for s in data["solicitations"]}
        assert ids == {"SOL-1"}

    def test_other_slices_untouched_by_a_refetch(self, tmp_path):
        path = tmp_path / "dashboard.json"
        jan = {"5340:2026-01": [_sol("JAN-1", fetched_at="2026-01-05T00:00:00+00:00", response_deadline="2026-08-01")]}
        export_json_by_slice(jan, path, source="sam.gov", today=_TODAY)

        feb = {"5340:2026-02": [_sol("FEB-1", fetched_at="2026-02-05T00:00:00+00:00", response_deadline="2026-08-01")]}
        export_json_by_slice(feb, path, source="sam.gov", today=_TODAY)

        data = json.loads(path.read_text(encoding="utf-8"))
        ids = {s["sol_number"] for s in data["solicitations"]}
        assert ids == {"JAN-1", "FEB-1"}

    def test_legacy_rows_without_slice_key_are_left_alone(self, tmp_path):
        path = tmp_path / "dashboard.json"
        path.write_text(
            json.dumps(
                {
                    "generated_at": "2026-01-01T00:00:00+00:00",
                    "source": "sam.gov",
                    "stats": {},
                    "solicitations": [_row(sol_number="LEGACY-1", response_deadline="2026-08-01")],
                }
            ),
            encoding="utf-8",
        )
        new = {"5340:2026-01": [_sol("SOL-1", fetched_at="2026-01-05T00:00:00+00:00", response_deadline="2026-08-01")]}
        export_json_by_slice(new, path, source="sam.gov", today=_TODAY)
        data = json.loads(path.read_text(encoding="utf-8"))
        ids = {s["sol_number"] for s in data["solicitations"]}
        assert ids == {"LEGACY-1", "SOL-1"}
