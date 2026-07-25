"""Unit tests for SQLite persistence and dedupe (uses tmp_path, no network)."""

import time

import pytest

from govscout.models import Solicitation
from govscout.store import Store


def make_sol(sol_number: str = "TEST-25-R-0001", **overrides) -> Solicitation:
    """Build a minimal Solicitation for store tests."""
    defaults = dict(
        sol_number=sol_number,
        title="Test Item",
        agency="DLA Aviation",
        psc_code="5340",
        posted_date="2025-02-18",
        response_deadline="2025-03-14",
        description="Request for quotation, NSN 5340-01-234-5678, qty 10 ea.",
        url="https://sam.gov/opp/x/view",
        attachments=["rfq.pdf"],
        nsns=["5340-01-234-5678"],
        part_numbers=["ABC-1"],
        quantities=[10],
        pricing_score=20,
        pricing_flags=["quote_requested"],
        fetched_at="2025-02-18T12:00:00+00:00",
    )
    defaults.update(overrides)
    return Solicitation(**defaults)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


class TestUpsert:
    def test_insert_returns_true_for_new(self, store):
        assert store.upsert(make_sol()) is True
        assert store.count() == 1

    def test_duplicate_sol_number_updates_not_duplicates(self, store):
        assert store.upsert(make_sol(title="Original")) is True
        assert store.upsert(make_sol(title="Amended")) is False
        assert store.count() == 1
        assert store.all()[0].title == "Amended"

    def test_upsert_many_reports_new_and_updated(self, store):
        sols = [make_sol(f"SOL-{i}") for i in range(3)]
        assert store.upsert_many(sols) == (3, 0)
        assert store.upsert_many(sols) == (0, 3)
        assert store.count() == 3


class TestQuery:
    def test_all_round_trips_fields(self, store):
        store.upsert(make_sol())
        sol = store.all()[0]
        assert sol.sol_number == "TEST-25-R-0001"
        assert sol.nsns == ["5340-01-234-5678"]
        assert sol.part_numbers == ["ABC-1"]
        assert sol.quantities == [10]
        assert sol.pricing_score == 20
        assert sol.pricing_flags == ["quote_requested"]
        assert sol.psc_code == "5340"

    def test_all_sorted_by_score_desc(self, store):
        store.upsert(make_sol("LOW", pricing_score=10))
        store.upsert(make_sol("HIGH", pricing_score=90))
        assert [s.sol_number for s in store.all()] == ["HIGH", "LOW"]

    def test_new_since_filters_by_first_seen(self, store):
        from datetime import datetime, timezone

        store.upsert(make_sol("OLD"))
        cutoff = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        time.sleep(0.01)
        store.upsert(make_sol("NEW"))
        recent = store.new_since(cutoff)
        assert [s.sol_number for s in recent] == ["NEW"]

    def test_update_preserves_first_seen(self, store):
        from datetime import datetime, timezone

        store.upsert(make_sol("X"))
        time.sleep(0.01)
        store.upsert(make_sol("X", title="v2"))
        cutoff = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        assert store.new_since(cutoff) == []

    def test_empty_store(self, store):
        assert store.count() == 0
        assert store.all() == []
        assert store.new_since("2000-01-01") == []
