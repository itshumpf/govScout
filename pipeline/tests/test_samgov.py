"""Unit tests for the SAM.gov source's pagination cap (no network; session is stubbed)."""

from govscout.sources.samgov import SamGovSource

_FULL_PAGE = [{"solicitationNumber": f"SOL-{i}", "title": "x"} for i in range(100)]


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.status_code = 200
        self._payload = payload
        self.text = ""

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
