"""Description enrichment: dereference SAM.gov's noticedesc link into real
narrative text before scoring, under a small daily request budget.

SAM.gov's v2 search response returns ``description`` as a link to the
narrative rather than the text itself (see sources/samgov.py's
notice_id_from_description / fetch_description). Left alone, the scorer and
NSN/part/quantity extractors only ever see the title. This module fills the
gap for as many freshly-fetched records as the day's request budget allows:
prioritizing the newest/most time-sensitive records first, reusing any
description already on file so a notice id is never fetched twice, and
stopping cleanly (not erroring through the rest of the budget) the moment
the quota is exhausted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .sources.samgov import SamGovError, SamGovRateLimitError, notice_id_from_description


@dataclass
class DescribeResult:
    """Outcome of one enrichment pass.

    Surfaced by the caller (see __main__.py) so partial coverage is always
    visible in run output and dashboard stats — nobody should be able to
    mistake "some records enriched" for "all records enriched".
    """

    candidates: int = 0  # records whose description was a link at all
    cached: int = 0  # resolved from a previously-stored description, no request spent
    fetched: int = 0  # resolved via a live request this run
    failed: int = 0  # a live request failed for a reason other than quota
    quota_exhausted: bool = False


def is_dereferenced(description: str) -> bool:
    """True if ``description`` already holds real narrative text rather than
    a noticedesc link.

    Callers building a ``cached_description`` lookup (see
    :func:`enrich_descriptions`) must run any candidate cached value through
    this before returning it — a description stored from a run that hit the
    quota before reaching this record is still just the bare link, and
    treating that as a resolved cache hit would silently "enrich" a record
    with the same link it started with.
    """
    return bool(description) and notice_id_from_description(description) is None


def prioritize(records: list[dict]) -> list[dict]:
    """Order raw records for description spend: newest postings first, soonest
    deadline breaking ties.

    There's no way to know which solicitation is actually worth a request
    before its text is fetched — dollar value, agency history, etc. aren't
    available from the search response. Recency plus deadline urgency is a
    cheap, defensible stand-in: newest postings are the ones a user hasn't
    had a chance to act on yet, and among same-day postings the ones with
    the least runway matter most.
    """
    by_deadline = sorted(records, key=lambda r: r.get("response_deadline") or "9999-99-99")
    return sorted(by_deadline, key=lambda r: r.get("posted_date") or "", reverse=True)


def enrich_descriptions(
    raw_records: list[dict],
    fetch_description: Callable[[str], str],
    cached_description: Callable[[dict], str | None],
    budget: int,
) -> DescribeResult:
    """Fill in ``description`` in place for records whose description is a
    noticedesc link, spending at most ``budget`` live requests.

    Records are processed in the order given — call :func:`prioritize` first
    if request order should favor certain records. ``description_enriched``
    on the resulting Solicitation is derived later, from whether
    ``description`` still looks like a link (see sources/base.py) — this
    function doesn't need to track it separately.

    ``cached_description(raw)`` should return the already-known description
    for this record (e.g. from the local store), or None if there isn't one
    — a cache hit costs no budget and is still resolved even after the
    quota is exhausted, since it requires no request at all. Any value it
    returns is still checked with :func:`is_dereferenced` before being
    trusted, so a caller that accidentally hands back a description that's
    itself still a bare link (e.g. a previous run left it unenriched when
    its budget ran out) falls through to a live fetch instead of being
    mistaken for a resolved cache hit.

    A ``SamGovRateLimitError`` from ``fetch_description`` stops all further
    *live* attempts for the rest of this call (cache hits keep working) —
    that's the "degrade gracefully" behavior: no more requests are attempted
    once the quota is known to be gone. Any other ``SamGovError`` is a
    per-record failure: it's logged in the result and the next record is
    tried, since one bad notice id shouldn't cost the rest of the budget.
    """
    result = DescribeResult()
    for raw in raw_records:
        notice_id = notice_id_from_description(raw.get("description") or "")
        if notice_id is None:
            continue  # already real text — sample data, or enriched by an earlier run
        result.candidates += 1

        cached = cached_description(raw)
        if cached is not None and is_dereferenced(cached):
            raw["description"] = cached
            result.cached += 1
            continue

        if result.quota_exhausted or budget <= 0:
            continue

        budget -= 1
        try:
            raw["description"] = fetch_description(notice_id)
            result.fetched += 1
        except SamGovRateLimitError:
            result.quota_exhausted = True
        except SamGovError:
            result.failed += 1

    return result
