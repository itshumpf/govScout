"""Source interface shared by all opportunity sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

from ..detect import score_pricing
from ..extract import extract_nsns, extract_part_numbers, extract_quantities
from ..models import Solicitation

# SAM.gov's v2 search response returns "description" as a link to the
# narrative rather than the text itself (see sources/samgov.py's
# notice_id_from_description/fetch_description/describe.py). Duplicated
# here as a plain string rather than imported to avoid a base.py <->
# samgov.py import cycle (samgov.py already imports from base.py) — this
# marker is a stable detail of SAM.gov's API, not something expected to
# change independently of samgov.py's own copy.
_NOTICEDESC_MARKER = "/opportunities/v1/noticedesc"


class Source(ABC):
    """Abstract opportunity source.

    Implementations fetch raw dicts from an upstream system and normalize
    them into Solicitation records. New adapters (e.g. an authenticated
    DIBBS scraper) plug in by subclassing this interface.
    """

    name: str = "source"

    @abstractmethod
    def fetch(self, psc_codes: list[str], days_back: int) -> list[dict]:
        """Fetch raw opportunity dicts filtered by PSC/FSC codes and recency."""
        ...

    @abstractmethod
    def normalize(self, raw: dict) -> Solicitation:
        """Convert one raw opportunity dict into a Solicitation."""
        ...


def enrich_solicitation(raw: dict) -> Solicitation:
    """Build a fully-enriched Solicitation from a normalized raw dict.

    Shared by all sources: runs field extraction (NSN / part number / qty)
    over title + description, and pricing-signal detection over the
    description plus attachment names.

    Expected raw keys: sol_number, title, agency, psc_code, posted_date,
    response_deadline, description, url, attachments.
    """
    title = raw.get("title") or ""
    description = raw.get("description") or ""
    attachments = list(raw.get("attachments") or [])
    searchable = f"{title}\n{description}"
    score, flags = score_pricing(searchable, attachments)
    return Solicitation(
        sol_number=raw["sol_number"],
        title=title,
        agency=raw.get("agency") or "",
        psc_code=raw.get("psc_code") or None,
        posted_date=raw.get("posted_date") or "",
        response_deadline=raw.get("response_deadline") or None,
        description=description,
        url=raw.get("url") or None,
        attachments=attachments,
        nsns=extract_nsns(searchable),
        part_numbers=extract_part_numbers(searchable),
        quantities=extract_quantities(searchable),
        pricing_score=score,
        pricing_flags=flags,
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        description_enriched=bool(description) and _NOTICEDESC_MARKER not in description,
    )
