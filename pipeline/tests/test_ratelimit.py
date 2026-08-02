"""Tests for cross-run 429 backoff (offline, no network)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from govscout import ratelimit


class TestRateLimitState:
    def test_no_marker_is_not_cooling_down(self):
        state = ratelimit.RateLimitState()
        assert state.cooling_down() is False
        assert state.retry_after() is None

    def test_recent_429_is_cooling_down(self):
        now = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
        state = ratelimit.RateLimitState(last_429_at=now - timedelta(hours=1))
        assert state.cooling_down(now=now) is True

    def test_429_older_than_24h_is_not_cooling_down(self):
        now = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
        state = ratelimit.RateLimitState(last_429_at=now - timedelta(hours=25))
        assert state.cooling_down(now=now) is False

    def test_exactly_24h_is_not_cooling_down(self):
        now = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
        state = ratelimit.RateLimitState(last_429_at=now - timedelta(hours=24))
        assert state.cooling_down(now=now) is False

    def test_retry_after_is_429_plus_24h(self):
        when = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
        state = ratelimit.RateLimitState(last_429_at=when)
        assert state.retry_after() == when + timedelta(hours=24)


class TestPersistence:
    def test_missing_file_loads_empty(self, tmp_path):
        state = ratelimit.load(tmp_path / "does-not-exist.json")
        assert state.last_429_at is None

    def test_record_and_load_roundtrip(self, tmp_path):
        path = tmp_path / "rate_limit_state.json"
        when = datetime(2026, 8, 2, 9, 30, tzinfo=timezone.utc)
        ratelimit.record_429(path, when=when)
        loaded = ratelimit.load(path)
        assert loaded.last_429_at == when

    def test_corrupt_file_loads_as_empty_not_fatal(self, tmp_path):
        path = tmp_path / "rate_limit_state.json"
        path.write_text("{not valid json", encoding="utf-8")
        state = ratelimit.load(path)
        assert state.last_429_at is None

    def test_clear_removes_marker(self, tmp_path):
        path = tmp_path / "rate_limit_state.json"
        ratelimit.record_429(path)
        assert path.exists()
        ratelimit.clear(path)
        assert not path.exists()

    def test_clear_on_missing_file_does_not_raise(self, tmp_path):
        ratelimit.clear(tmp_path / "does-not-exist.json")  # must not raise
