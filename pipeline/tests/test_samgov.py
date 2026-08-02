"""Unit tests for the SAM.gov source's pagination cap and 429 backoff (no network; session is stubbed)."""

from datetime import datetime, timedelta, timezone

import pytest

from govscout import ratelimit
from govscout.sources.samgov import SamGovRateLimitError, SamGovSource

_FULL_PAGE = [{"solicitationNumber": f"SOL-{i}", "title": "x"} for i in range(100)]


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> dict:
        return self._payload


class _FakeSession:
    """Stub requests.Session that always returns a full page (would paginate forever)."""

    def __init__(self) -> None:
        self.calls = 0
        self.headers: dict[str, str] = {}

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return _FakeResponse({"opportunitiesData": _FULL_PAGE})


class TestMaxPagesCap:
    def test_default_max_pages_is_one_request(self, monkeypatch):
        monkeypatch.setattr("govscout.sources.samgov.time.sleep", lambda *_: None)
        session = _FakeSession()
        source = SamGovSource("key", session=session)
        raw = source.fetch([], 30)
        assert session.calls == 1
        assert len(raw) == 100

    def test_max_pages_limits_requests(self, monkeypatch):
        monkeypatch.setattr("govscout.sources.samgov.time.sleep", lambda *_: None)
        session = _FakeSession()
        source = SamGovSource("key", session=session, max_pages=3)
        raw = source.fetch([], 30)
        assert session.calls == 3
        assert len(raw) == 300

    def test_short_page_stops_before_max_pages(self, monkeypatch):
        monkeypatch.setattr("govscout.sources.samgov.time.sleep", lambda *_: None)

        class _ShortSession(_FakeSession):
            def get(self, url, params=None, timeout=None):
                self.calls += 1
                return _FakeResponse({"opportunitiesData": _FULL_PAGE[:10]})

        session = _ShortSession()
        source = SamGovSource("key", session=session, max_pages=5)
        raw = source.fetch([], 30)
        assert session.calls == 1
        assert len(raw) == 10


class _RateLimitedSession(_FakeSession):
    """Stub session that always returns HTTP 429."""

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return _FakeResponse({}, status_code=429)


class TestCrossRunRateLimitBackoff:
    def test_real_429_is_persisted_to_state_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr("govscout.sources.samgov.time.sleep", lambda *_: None)
        state_path = tmp_path / "rate_limit_state.json"
        source = SamGovSource("key", session=_RateLimitedSession(), state_path=state_path)

        with pytest.raises(SamGovRateLimitError):
            source.fetch([], 30)

        state = ratelimit.load(state_path)
        assert state.last_429_at is not None

    def test_second_call_within_24h_skips_the_request_entirely(self, tmp_path, monkeypatch):
        monkeypatch.setattr("govscout.sources.samgov.time.sleep", lambda *_: None)
        state_path = tmp_path / "rate_limit_state.json"
        ratelimit.record_429(state_path, when=datetime.now(timezone.utc) - timedelta(hours=1))

        session = _FakeSession()  # would happily return 200 if called
        source = SamGovSource("key", session=session, state_path=state_path)

        with pytest.raises(SamGovRateLimitError):
            source.fetch([], 30)

        assert session.calls == 0  # never made a live request

    def test_call_after_cooldown_expires_proceeds_normally(self, tmp_path, monkeypatch):
        monkeypatch.setattr("govscout.sources.samgov.time.sleep", lambda *_: None)
        state_path = tmp_path / "rate_limit_state.json"
        ratelimit.record_429(state_path, when=datetime.now(timezone.utc) - timedelta(hours=25))

        session = _FakeSession()
        source = SamGovSource("key", session=session, state_path=state_path)
        raw = source.fetch([], 30)

        assert session.calls == 1
        assert len(raw) == 100

    def test_successful_call_clears_a_stale_marker(self, tmp_path, monkeypatch):
        monkeypatch.setattr("govscout.sources.samgov.time.sleep", lambda *_: None)
        state_path = tmp_path / "rate_limit_state.json"
        ratelimit.record_429(state_path, when=datetime.now(timezone.utc) - timedelta(hours=25))

        source = SamGovSource("key", session=_FakeSession(), state_path=state_path)
        source.fetch([], 30)

        assert not state_path.exists()

    def test_without_state_path_behaves_exactly_as_before(self, monkeypatch):
        """state_path is opt-in — omitting it must not change existing behavior."""
        monkeypatch.setattr("govscout.sources.samgov.time.sleep", lambda *_: None)
        source = SamGovSource("key", session=_FakeSession())
        raw = source.fetch([], 30)
        assert len(raw) == 100
