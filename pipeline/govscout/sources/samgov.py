"""Live SAM.gov Opportunities API v2 client.

Docs: https://open.gsa.gov/api/get-opportunities-public-api/
Requires a free api.data.gov key (SAM_API_KEY env var or --api-key).
"""

from __future__ import annotations

import html
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

from .. import ratelimit
from ..models import Solicitation
from .base import Source, enrich_solicitation

BASE_URL = "https://api.sam.gov/opportunities/v2/search"

# The search response's "description" field is not the narrative text — it's
# a link to it (confirmed against a live response, 2026-08-03: every field
# is a fixed-length noticedesc URL, e.g.
# "https://api.sam.gov/prod/opportunities/v1/noticedesc?noticeid=<id>").
# fetch_description() dereferences one; notice_id_from_description() picks
# the id back out of the link so callers can tell "needs enrichment" apart
# from "already real text" (sample data, or an earlier enrichment pass).
DESC_URL = "https://api.sam.gov/prod/opportunities/v1/noticedesc"
_NOTICEDESC_MARKER = "/opportunities/v1/noticedesc"
_TAG_RE = re.compile(r"<[^>]+>")

_PAGE_LIMIT = 100  # API max per page
_TIMEOUT = 30
_PAGE_DELAY = 0.5  # seconds between page requests (politeness)
_MAX_RETRIES = 3
_RETRY_STATUSES = {500, 502, 503, 504}
# 429 is NOT retried: personal keys have a small daily quota, and retrying a
# rate-limited request just burns the rest of the day's allowance.
#
# CONFIRMED (Braeden, 2026-08-02): 10 requests/day for this key, a personal
# key without an entity role. Not publicly documented — open.gsa.gov's
# Opportunities API docs only say "Request per day are limited based on the
# federal or non-federal or general roles" without a figure — but this one
# is a real confirmed number, not the "~10, unconfirmed" guess this comment
# used to carry. Expected to rise once Braeden's EIN/entity-role
# verification comes through; nothing in the code needs to change for that
# beyond raising sync_max_pages/max_pages in config.json when it happens.
RATE_LIMIT_MSG = (
    "SAM.gov rate limit reached (HTTP 429).\n"
    "This key is capped at 10 requests/day (confirmed, not an estimate) until\n"
    "an entity-role upgrade raises it. The quota resets daily — try again\n"
    "later (and avoid repeated runs today, since each run spends from the\n"
    "same daily allowance)."
)

# Guidance shown when no API key is available.
API_KEY_HELP = (
    "No SAM.gov API key provided.\n"
    "Get a free key at https://api.data.gov/signup/ (or in your SAM.gov account\n"
    "under Workspace > API Key), then pass --api-key KEY or set SAM_API_KEY."
)


def notice_id_from_description(description: str) -> str | None:
    """Pull the noticeid back out of a noticedesc link, or None if ``description``
    isn't one (already-dereferenced text, sample data, empty string, ...)."""
    if _NOTICEDESC_MARKER not in (description or ""):
        return None
    ids = parse_qs(urlparse(description).query).get("noticeid")
    return ids[0] if ids else None


def _strip_html(raw_html: str) -> str:
    """Collapse noticedesc's HTML-wrapped text to plain whitespace-normalized text.

    Good enough for the scorer/extractors (which only care about words and
    punctuation, not markup) without pulling in an HTML-parsing dependency.
    """
    text = html.unescape(_TAG_RE.sub(" ", raw_html or ""))
    return " ".join(text.split())


class SamGovError(RuntimeError):
    """Raised when the SAM.gov API cannot be queried successfully."""


class SamGovRateLimitError(SamGovError):
    """Raised specifically for HTTP 429 (daily quota exhausted).

    A distinct subclass (rather than just SamGovError) so callers that need
    to react differently to "quota's gone, stop everything" versus "this one
    request failed for some other reason" can do so without string-matching
    the message. Still catchable as a plain SamGovError for existing callers.
    """


class SamGovSource(Source):
    """Source adapter for the SAM.gov Opportunities API v2.

    max_pages caps how many 100-record pages ``fetch`` will request in a
    single run — each page is one HTTP request against the daily quota.
    Personal api.data.gov keys without an entity role are capped at 10
    requests/day (confirmed), so the default of 1 keeps a normal run to a
    single request. An entity-role key is expected to raise the quota to
    ~1000/day (that figure itself is still an unconfirmed estimate); raise
    ``max_pages`` accordingly once you have one.

    state_path, if given, persists the timestamp of the last confirmed 429
    (see ratelimit.py) and makes ``fetch_range`` refuse to make a live
    request — no HTTP call at all — while still within the 24h cooldown.
    Without this, a fresh GitHub Actions checkout (or a human re-running the
    workflow a few times) has no memory of an earlier run's 429 and just
    burns another request to re-discover the same rate limit.
    """

    name = "sam.gov"

    def __init__(
        self,
        api_key: str,
        session: requests.Session | None = None,
        max_pages: int = 1,
        state_path: str | Path | None = None,
    ) -> None:
        if not api_key:
            raise SamGovError(API_KEY_HELP)
        self.api_key = api_key
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "govscout/0.1.0"})
        self.max_pages = max_pages
        self.state_path = state_path

    # ------------------------------------------------------------------ fetch

    def fetch(self, psc_codes: list[str], days_back: int) -> list[dict]:
        """Fetch matching opportunities posted within the last ``days_back`` days.

        Thin wrapper over :meth:`fetch_range` anchored to "today"; see that
        method for pagination/quota behavior.
        """
        today = datetime.now(timezone.utc).date()
        return self.fetch_range(psc_codes, today - timedelta(days=days_back), today)

    def fetch_range(self, psc_codes: list[str], posted_from: date, posted_to: date) -> list[dict]:
        """Fetch matching opportunities posted within an explicit date range.

        ``posted_from``/``posted_to`` are ``datetime.date``. Paginates up to
        ``self.max_pages`` pages (100 records each) — see the class
        docstring for the daily-quota rationale behind the default. This is
        the primitive both the weekday-rotation ``fetch`` and the coverage
        ledger's per-slice (code, month) backfill build on.

        Raises ``SamGovRateLimitError`` immediately, without any HTTP
        request, if ``state_path`` shows a 429 within the last 24h.
        """
        if self.state_path is not None:
            state = ratelimit.load(self.state_path)
            if state.cooling_down():
                raise SamGovRateLimitError(
                    f"Skipping SAM.gov request: still cooling down from a rate limit hit at "
                    f"{state.last_429_at.isoformat()} (24h backoff, retry after "
                    f"{state.retry_after().isoformat()}). No live request made — this doesn't "
                    f"spend any of today's quota."
                )

        params: dict[str, str | int] = {
            "api_key": self.api_key,
            "postedFrom": posted_from.strftime("%m/%d/%Y"),
            "postedTo": posted_to.strftime("%m/%d/%Y"),
            "limit": _PAGE_LIMIT,
            "offset": 0,
        }
        if psc_codes:
            params["psc"] = ",".join(code.strip() for code in psc_codes)

        raw: list[dict] = []
        page = 0
        while page < self.max_pages:
            payload = self._get(BASE_URL, params)
            opportunities = payload.get("opportunitiesData") or []
            raw.extend(self._map_raw(opp) for opp in opportunities)
            page += 1
            if len(opportunities) < _PAGE_LIMIT or page >= self.max_pages:
                break
            params["offset"] = int(params["offset"]) + _PAGE_LIMIT
            time.sleep(_PAGE_DELAY)

        if self.state_path is not None:
            ratelimit.clear(self.state_path)
        return raw

    # --------------------------------------------------------- description

    def fetch_description(self, notice_id: str) -> str:
        """Dereference one notice's noticedesc link into plain narrative text.

        A single request against the same daily quota as search (see the
        module-level DESC_URL note) — callers are responsible for budgeting
        how many of these they can afford; see describe.py.
        """
        payload = self._get(DESC_URL, {"api_key": self.api_key, "noticeid": notice_id})
        return _strip_html(payload.get("description") or "")

    def _get(self, url: str, params: dict[str, str | int]) -> dict:
        """GET one request with retry/backoff on transient 5xx responses.

        HTTP 429 raises immediately (see RATE_LIMIT_MSG) to protect the
        caller's small daily request quota.
        """
        delay = 1.0
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = self.session.get(url, params=params, timeout=_TIMEOUT)
            except requests.RequestException as exc:
                if attempt == _MAX_RETRIES:
                    raise SamGovError(f"SAM.gov request failed: {exc}") from exc
                time.sleep(delay)
                delay *= 2
                continue
            if resp.status_code == 429:
                if self.state_path is not None:
                    ratelimit.record_429(self.state_path)
                raise SamGovRateLimitError(RATE_LIMIT_MSG)
            if resp.status_code in _RETRY_STATUSES:
                if attempt == _MAX_RETRIES:
                    raise SamGovError(f"SAM.gov returned HTTP {resp.status_code} after {_MAX_RETRIES} attempts")
                time.sleep(delay)
                delay *= 2
                continue
            if resp.status_code != 200:
                raise SamGovError(f"SAM.gov returned HTTP {resp.status_code}: {resp.text[:200]}")
            try:
                return resp.json()
            except ValueError as exc:
                raise SamGovError("SAM.gov returned invalid JSON") from exc
        raise SamGovError("unreachable")  # pragma: no cover

    # -------------------------------------------------------------- normalize

    @staticmethod
    def _map_raw(opp: dict) -> dict:
        """Map SAM.gov API fields onto the common raw-dict shape."""
        agency_path = opp.get("fullParentPathName") or ""
        agency = agency_path.split(".")[-1].strip() if agency_path else ""
        sol_num = opp.get("solicitationNumber") or opp.get("noticeId") or ""
        notice_id = opp.get("noticeId") or ""
        ui_link = opp.get("uiLink") or ""

        if ui_link and ui_link.rstrip("/") != "https://sam.gov":
            target_url = ui_link
        elif notice_id:
            target_url = f"https://sam.gov/workspace/contract/opp/{notice_id}/view"
        elif sol_num:
            target_url = f"https://sam.gov/search/?index=opp&keywords={sol_num}"
        else:
            target_url = "https://sam.gov"

        return {
            "sol_number": sol_num,
            "title": opp.get("title") or "",
            "agency": agency,
            "psc_code": opp.get("classificationCode") or None,
            "posted_date": (opp.get("postedDate") or "")[:10],
            "response_deadline": (opp.get("responseDeadLine") or "")[:10] or None,
            "description": opp.get("description") or "",
            "url": target_url,
            # v2 search responses carry attachment links under "resourceLinks"
            # (array of opaque download URLs) — there is no "attachments"
            # field at all (confirmed against a live response 2026-08-03).
            "attachments": [link for link in (opp.get("resourceLinks") or []) if isinstance(link, str)],
        }

    def normalize(self, raw: dict) -> Solicitation:
        """Normalize a mapped SAM.gov record into an enriched Solicitation."""
        return enrich_solicitation(raw)
