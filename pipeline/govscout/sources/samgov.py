"""Live SAM.gov Opportunities API v2 client.

Docs: https://open.gsa.gov/api/get-opportunities-public-api/
Requires a free api.data.gov key (SAM_API_KEY env var or --api-key).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import requests

from ..models import Solicitation
from .base import Source, enrich_solicitation

BASE_URL = "https://api.sam.gov/opportunities/v2/search"
_PAGE_LIMIT = 100  # API max per page
_TIMEOUT = 30
_PAGE_DELAY = 0.5  # seconds between page requests (politeness)
_MAX_RETRIES = 3
_RETRY_STATUSES = {500, 502, 503, 504}
# 429 is NOT retried: free personal keys have a tiny daily quota (10 req/day),
# and retrying a rate-limited request just burns the rest of the day's allowance.
RATE_LIMIT_MSG = (
    "SAM.gov rate limit reached (HTTP 429).\n"
    "Personal keys without an entity role are limited to ~10 requests/day.\n"
    "The quota resets daily — try again later (and avoid repeated runs today,\n"
    "since each run spends from the same daily allowance)."
)

# Guidance shown when no API key is available.
API_KEY_HELP = (
    "No SAM.gov API key provided.\n"
    "Get a free key at https://api.data.gov/signup/ (or in your SAM.gov account\n"
    "under Workspace > API Key), then pass --api-key KEY or set SAM_API_KEY."
)


class SamGovError(RuntimeError):
    """Raised when the SAM.gov API cannot be queried successfully."""


class SamGovSource(Source):
    """Source adapter for the SAM.gov Opportunities API v2.

    max_pages caps how many 100-record pages ``fetch`` will request in a
    single run — each page is one HTTP request against the daily quota.
    Personal api.data.gov keys without an entity role are limited to ~10
    requests/day, so the default of 1 keeps a normal run to a single
    request. An entity-role key raises the quota to ~1000/day; raise
    ``max_pages`` accordingly once you have one.
    """

    name = "sam.gov"

    def __init__(
        self,
        api_key: str,
        session: requests.Session | None = None,
        max_pages: int = 1,
    ) -> None:
        if not api_key:
            raise SamGovError(API_KEY_HELP)
        self.api_key = api_key
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "govscout/0.1.0"})
        self.max_pages = max_pages

    # ------------------------------------------------------------------ fetch

    def fetch(self, psc_codes: list[str], days_back: int) -> list[dict]:
        """Fetch matching opportunities posted within the last ``days_back`` days.

        Paginates up to ``self.max_pages`` pages (100 records each) — see the
        class docstring for the daily-quota rationale behind the default.
        """
        today = datetime.now(timezone.utc).date()
        params: dict[str, str | int] = {
            "api_key": self.api_key,
            "postedFrom": (today - timedelta(days=days_back)).strftime("%m/%d/%Y"),
            "postedTo": today.strftime("%m/%d/%Y"),
            "limit": _PAGE_LIMIT,
            "offset": 0,
        }
        if psc_codes:
            params["psc"] = ",".join(code.strip() for code in psc_codes)

        raw: list[dict] = []
        page = 0
        while page < self.max_pages:
            payload = self._get(params)
            opportunities = payload.get("opportunitiesData") or []
            raw.extend(self._map_raw(opp) for opp in opportunities)
            page += 1
            if len(opportunities) < _PAGE_LIMIT or page >= self.max_pages:
                break
            params["offset"] = int(params["offset"]) + _PAGE_LIMIT
            time.sleep(_PAGE_DELAY)
        return raw

    def _get(self, params: dict[str, str | int]) -> dict:
        """GET one page with retry/backoff on transient 5xx responses.

        HTTP 429 raises immediately (see RATE_LIMIT_MSG) to protect the
        caller's small daily request quota.
        """
        delay = 1.0
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = self.session.get(BASE_URL, params=params, timeout=_TIMEOUT)
            except requests.RequestException as exc:
                if attempt == _MAX_RETRIES:
                    raise SamGovError(f"SAM.gov request failed: {exc}") from exc
                time.sleep(delay)
                delay *= 2
                continue
            if resp.status_code == 429:
                raise SamGovError(RATE_LIMIT_MSG)
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
        return {
            "sol_number": opp.get("solicitationNumber") or opp.get("noticeId") or "",
            "title": opp.get("title") or "",
            "agency": agency,
            "psc_code": opp.get("classificationCode") or None,
            "posted_date": (opp.get("postedDate") or "")[:10],
            "response_deadline": (opp.get("responseDeadLine") or "")[:10] or None,
            "description": opp.get("description") or "",
            "url": opp.get("uiLink") or None,
            "attachments": [a.get("name", "") for a in (opp.get("attachments") or []) if isinstance(a, dict)],
        }

    def normalize(self, raw: dict) -> Solicitation:
        """Normalize a mapped SAM.gov record into an enriched Solicitation."""
        return enrich_solicitation(raw)
