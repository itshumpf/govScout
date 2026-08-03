"""Unit tests for description dereferencing (offline; fetch_description is stubbed)."""

from __future__ import annotations

from govscout.describe import DescribeResult, enrich_descriptions, is_dereferenced, prioritize
from govscout.sources.samgov import SamGovError, SamGovRateLimitError

_LINK = "https://api.sam.gov/prod/opportunities/v1/noticedesc?noticeid={}"


def _link_record(sol_number: str, notice_id: str, **overrides) -> dict:
    record = {
        "sol_number": sol_number,
        "title": "x",
        "description": _LINK.format(notice_id),
        "posted_date": "2026-08-01",
        "response_deadline": None,
    }
    record.update(overrides)
    return record


class FakeFetcher:
    """Stub fetch_description: records calls, can be told to 429 on a given id."""

    def __init__(self, rate_limited_on: str | None = None, errors_on: str | None = None) -> None:
        self.calls: list[str] = []
        self._rate_limited_on = rate_limited_on
        self._errors_on = errors_on

    def __call__(self, notice_id: str) -> str:
        self.calls.append(notice_id)
        if notice_id == self._rate_limited_on:
            raise SamGovRateLimitError("quota gone")
        if notice_id == self._errors_on:
            raise SamGovError("bad notice id")
        return f"Real text for {notice_id}"


def _no_cache(_raw: dict) -> str | None:
    return None


class TestIsDereferenced:
    def test_link_is_not_dereferenced(self):
        assert is_dereferenced(_LINK.format("abc")) is False

    def test_real_text_is_dereferenced(self):
        assert is_dereferenced("Request for quotation, NSN 5340-01-234-5678.") is True

    def test_empty_string_is_not_dereferenced(self):
        assert is_dereferenced("") is False


class TestPrioritize:
    def test_newest_posted_date_first(self):
        records = [
            {"posted_date": "2026-07-01", "response_deadline": None},
            {"posted_date": "2026-08-01", "response_deadline": None},
            {"posted_date": "2026-07-15", "response_deadline": None},
        ]
        ordered = prioritize(records)
        assert [r["posted_date"] for r in ordered] == ["2026-08-01", "2026-07-15", "2026-07-01"]

    def test_soonest_deadline_breaks_same_day_ties(self):
        records = [
            {"posted_date": "2026-08-01", "response_deadline": "2026-09-01", "id": "far"},
            {"posted_date": "2026-08-01", "response_deadline": "2026-08-10", "id": "near"},
        ]
        ordered = prioritize(records)
        assert [r["id"] for r in ordered] == ["near", "far"]

    def test_missing_deadline_sorts_after_any_real_deadline(self):
        records = [
            {"posted_date": "2026-08-01", "response_deadline": None, "id": "no-deadline"},
            {"posted_date": "2026-08-01", "response_deadline": "2026-08-10", "id": "has-deadline"},
        ]
        ordered = prioritize(records)
        assert [r["id"] for r in ordered] == ["has-deadline", "no-deadline"]


class TestEnrichDescriptions:
    def test_fetches_each_candidate_up_to_budget(self):
        records = [_link_record("SOL-1", "n1"), _link_record("SOL-2", "n2")]
        fetcher = FakeFetcher()
        result = enrich_descriptions(records, fetcher, _no_cache, budget=5)

        assert fetcher.calls == ["n1", "n2"]
        assert records[0]["description"] == "Real text for n1"
        assert records[1]["description"] == "Real text for n2"
        assert result == DescribeResult(candidates=2, cached=0, fetched=2, failed=0, quota_exhausted=False)

    def test_records_with_real_text_already_are_not_candidates(self):
        records = [{"sol_number": "SOL-1", "description": "Already real narrative text."}]
        fetcher = FakeFetcher()
        result = enrich_descriptions(records, fetcher, _no_cache, budget=5)

        assert fetcher.calls == []
        assert result.candidates == 0
        assert records[0]["description"] == "Already real narrative text."

    def test_cache_hit_skips_the_live_request_entirely(self):
        records = [_link_record("SOL-1", "n1")]

        def cached(raw: dict) -> str | None:
            return "Cached real text" if raw["sol_number"] == "SOL-1" else None

        fetcher = FakeFetcher()
        result = enrich_descriptions(records, fetcher, cached, budget=5)

        assert fetcher.calls == []  # no request spent
        assert records[0]["description"] == "Cached real text"
        assert result.cached == 1
        assert result.fetched == 0
        assert result.candidates == 1

    def test_cache_miss_falls_through_to_a_live_fetch(self):
        records = [_link_record("SOL-1", "n1")]
        fetcher = FakeFetcher()
        result = enrich_descriptions(records, fetcher, _no_cache, budget=5)

        assert fetcher.calls == ["n1"]
        assert result.cached == 0
        assert result.fetched == 1

    def test_budget_zero_uses_cache_but_makes_no_live_requests(self):
        records = [_link_record("SOL-1", "n1"), _link_record("SOL-2", "n2")]

        def cached(raw: dict) -> str | None:
            return "Cached text" if raw["sol_number"] == "SOL-1" else None

        fetcher = FakeFetcher()
        result = enrich_descriptions(records, fetcher, cached, budget=0)

        assert fetcher.calls == []
        assert records[0]["description"] == "Cached text"  # resolved from cache, free
        assert records[1]["description"] == _LINK.format("n2")  # still a link — no budget left
        assert result.cached == 1
        assert result.fetched == 0

    def test_quota_exhaustion_stops_further_live_attempts_cleanly(self):
        records = [
            _link_record("SOL-1", "n1"),
            _link_record("SOL-2", "n2"),  # this one 429s
            _link_record("SOL-3", "n3"),
            _link_record("SOL-4", "n4"),
        ]
        fetcher = FakeFetcher(rate_limited_on="n2")
        result = enrich_descriptions(records, fetcher, _no_cache, budget=10)

        # Stops at the 429 — n3/n4 are never even attempted, despite budget left.
        assert fetcher.calls == ["n1", "n2"]
        assert records[0]["description"] == "Real text for n1"
        assert records[1]["description"] == _LINK.format("n2")  # unchanged — the attempt failed
        assert records[2]["description"] == _LINK.format("n3")  # never attempted
        assert records[3]["description"] == _LINK.format("n4")  # never attempted
        assert result.fetched == 1
        assert result.quota_exhausted is True

    def test_cache_hits_still_resolve_after_quota_is_exhausted(self):
        """A cache hit costs no request, so it should keep working even once
        live fetching has stopped — this is what makes the degradation
        graceful rather than "everything after the 429 is now broken"."""
        records = [
            _link_record("SOL-1", "n1"),  # 429s
            _link_record("SOL-2", "n2"),  # cached
        ]

        def cached(raw: dict) -> str | None:
            return "Cached text for SOL-2" if raw["sol_number"] == "SOL-2" else None

        fetcher = FakeFetcher(rate_limited_on="n1")
        result = enrich_descriptions(records, fetcher, cached, budget=10)

        assert fetcher.calls == ["n1"]  # never even tries n2 live
        assert records[1]["description"] == "Cached text for SOL-2"
        assert result.quota_exhausted is True
        assert result.cached == 1

    def test_a_cached_value_that_is_still_a_link_is_not_treated_as_resolved(self):
        """Guards against a subtle bug: if an earlier run stored a description
        that was itself never dereferenced (ran out of budget that day), a
        naive cache must not treat that link as a valid cache hit — it
        must fall through to a live fetch attempt instead of silently
        "enriching" the record with the same link it started with."""
        records = [_link_record("SOL-1", "n1")]

        def stale_cache(raw: dict) -> str | None:
            return _LINK.format("n1")  # a "cached" value that's still just the link

        fetcher = FakeFetcher()
        result = enrich_descriptions(records, fetcher, stale_cache, budget=5)

        assert fetcher.calls == ["n1"]  # fell through to a live fetch, not a fake cache hit
        assert records[0]["description"] == "Real text for n1"
        assert result.cached == 0
        assert result.fetched == 1

    def test_non_quota_failure_is_skipped_and_the_rest_of_the_batch_continues(self):
        records = [_link_record("SOL-1", "bad"), _link_record("SOL-2", "n2")]
        fetcher = FakeFetcher(errors_on="bad")
        result = enrich_descriptions(records, fetcher, _no_cache, budget=5)

        assert fetcher.calls == ["bad", "n2"]
        assert records[0]["description"] == _LINK.format("bad")  # left as the link, not crashed
        assert records[1]["description"] == "Real text for n2"
        assert result.failed == 1
        assert result.fetched == 1
        assert result.quota_exhausted is False

    def test_empty_records_list(self):
        result = enrich_descriptions([], FakeFetcher(), _no_cache, budget=5)
        assert result == DescribeResult()
