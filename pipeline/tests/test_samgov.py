"""Unit tests for the SAM.gov source's pagination cap and 429 backoff (no network; session is stubbed)."""

from datetime import datetime, timedelta, timezone

import pytest

from govscout import ratelimit
from govscout.sources.samgov import DESC_URL, SamGovRateLimitError, SamGovSource, notice_id_from_description

_FULL_PAGE = [{"solicitationNumber": f"SOL-{i}", "title": "x"} for i in range(100)]


class TestMapRawAttachments:
    """v2 search responses carry attachment links under "resourceLinks" (array
    of opaque download URLs) — there is no "attachments" field at all. See
    samgov.py's _map_raw comment; confirmed against a live response."""

    def test_resource_links_mapped_to_attachments(self):
        opp = {
            "solicitationNumber": "SOL-1",
            "title": "x",
            "resourceLinks": [
                "https://sam.gov/api/prod/opps/v3/opportunities/resources/files/abc/download",
                "https://sam.gov/api/prod/opps/v3/opportunities/resources/files/def/download",
            ],
        }
        raw = SamGovSource._map_raw(opp)
        assert raw["attachments"] == [
            "https://sam.gov/api/prod/opps/v3/opportunities/resources/files/abc/download",
            "https://sam.gov/api/prod/opps/v3/opportunities/resources/files/def/download",
        ]

    def test_missing_resource_links_is_empty(self):
        raw = SamGovSource._map_raw({"solicitationNumber": "SOL-1", "title": "x"})
        assert raw["attachments"] == []

    def test_null_resource_links_is_empty(self):
        raw = SamGovSource._map_raw({"solicitationNumber": "SOL-1", "title": "x", "resourceLinks": None})
        assert raw["attachments"] == []


class TestNoticeIdFromDescription:
    def test_extracts_id_from_noticedesc_link(self):
        url = "https://api.sam.gov/prod/opportunities/v1/noticedesc?noticeid=f92cdccc3f184324860a89e49a5cf6e7"
        assert notice_id_from_description(url) == "f92cdccc3f184324860a89e49a5cf6e7"

    def test_real_narrative_text_is_not_a_link(self):
        assert notice_id_from_description("Request for quotation, NSN 5340-01-234-5678.") is None

    def test_empty_and_none_are_not_links(self):
        assert notice_id_from_description("") is None
        assert notice_id_from_description(None) is None


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


class _DescSession:
    """Stub session for fetch_description: records the URL/params it was called with."""

    def __init__(self, description_html: str = "<p>Real text.</p>", status_code: int = 200) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._description_html = description_html
        self._status_code = status_code
        self.headers: dict[str, str] = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return _FakeResponse({"description": self._description_html}, status_code=self._status_code)


class TestFetchDescription:
    """SamGovSource.fetch_description — the noticedesc dereference itself."""

    def test_hits_the_noticedesc_endpoint_with_api_key_and_noticeid(self):
        session = _DescSession()
        source = SamGovSource("mykey", session=session)
        source.fetch_description("abc123")

        assert len(session.calls) == 1
        url, params = session.calls[0]
        assert url == DESC_URL
        assert params == {"api_key": "mykey", "noticeid": "abc123"}

    def test_strips_html_and_normalizes_whitespace(self):
        html = "<p><strong>Subject: RFQ</strong></p>\n\n<p>Enclosed is a&nbsp;Request  for   Quotations.</p>"
        session = _DescSession(description_html=html)
        source = SamGovSource("key", session=session)

        text = source.fetch_description("abc123")
        assert text == "Subject: RFQ Enclosed is a Request for Quotations."

    def test_missing_description_in_payload_returns_empty_string(self):
        session = _DescSession()
        session._description_html = None  # payload has no "description" key content
        source = SamGovSource("key", session=session)
        assert source.fetch_description("abc123") == ""

    def test_429_raises_rate_limit_error_and_records_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr("govscout.sources.samgov.time.sleep", lambda *_: None)
        state_path = tmp_path / "rate_limit_state.json"
        session = _DescSession(status_code=429)
        source = SamGovSource("key", session=session, state_path=state_path)

        with pytest.raises(SamGovRateLimitError):
            source.fetch_description("abc123")

        assert ratelimit.load(state_path).last_429_at is not None

    def test_does_not_consume_a_search_page_budget(self):
        """fetch_description is a distinct request from fetch_range's paginated
        search calls — hitting it must not touch max_pages/offset state."""
        session = _DescSession()
        source = SamGovSource("key", session=session, max_pages=1)
        source.fetch_description("abc123")
        source.fetch_description("def456")
        assert len(session.calls) == 2
