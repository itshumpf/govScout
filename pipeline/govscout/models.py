"""Core data model for GovScout."""

from __future__ import annotations

from dataclasses import dataclass

# Canonical column order for CSV export and the SQLite table.
ROW_FIELDS: tuple[str, ...] = (
    "sol_number",
    "title",
    "agency",
    "psc_code",
    "posted_date",
    "response_deadline",
    "url",
    "attachments",
    "nsns",
    "part_numbers",
    "quantities",
    "pricing_score",
    "pricing_flags",
    "description",
    "description_enriched",
    "fetched_at",
)

_LIST_FIELDS = ("attachments", "nsns", "part_numbers", "quantities", "pricing_flags")


@dataclass
class Solicitation:
    """A normalized federal solicitation/opportunity record.

    List fields (nsns, part_numbers, quantities, pricing_flags, attachments)
    are populated by the extraction/detection pipeline during normalization.
    """

    sol_number: str  # unique id / solicitation number
    title: str
    agency: str
    psc_code: str | None  # PSC/FSC code if present
    posted_date: str  # ISO YYYY-MM-DD
    response_deadline: str | None
    description: str
    url: str | None
    attachments: list[str]  # attachment filenames/urls
    nsns: list[str]  # extracted NSNs
    part_numbers: list[str]  # extracted part numbers
    quantities: list[int]  # extracted quantities
    pricing_score: int  # 0-100, from detect.py
    pricing_flags: list[str]  # e.g. ["online_pricing", "historical_pricing", "quote_requested"]
    fetched_at: str  # ISO timestamp
    description_enriched: bool = False  # True when `description` holds real narrative
    # text rather than a SAM.gov noticedesc link — see sources/base.py and
    # describe.py. Computed from the description content itself, so it's
    # correct regardless of source (sample data is always real text).

    def to_row(self) -> dict:
        """Flatten to a CSV/DB-friendly dict.

        List fields are joined with "; ". Returns keys in ROW_FIELDS order.
        """
        return {
            "sol_number": self.sol_number,
            "title": self.title,
            "agency": self.agency,
            "psc_code": self.psc_code or "",
            "posted_date": self.posted_date,
            "response_deadline": self.response_deadline or "",
            "url": self.url or "",
            "attachments": "; ".join(self.attachments),
            "nsns": "; ".join(self.nsns),
            "part_numbers": "; ".join(self.part_numbers),
            "quantities": "; ".join(str(q) for q in self.quantities),
            "pricing_score": self.pricing_score,
            "pricing_flags": "; ".join(self.pricing_flags),
            "description": self.description,
            "description_enriched": self.description_enriched,
            "fetched_at": self.fetched_at,
        }

    @classmethod
    def from_row(cls, row: dict) -> Solicitation:
        """Rebuild a Solicitation from a flat row produced by :meth:`to_row`."""

        def split_list(value: str | None) -> list[str]:
            if not value:
                return []
            return [part.strip() for part in str(value).split(";") if part.strip()]

        def parse_bool(value: object) -> bool:
            """Handle sqlite (0/1 int), JSON (bool), and CSV (stringified) round-trips."""
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true")
            return bool(value)

        return cls(
            sol_number=row["sol_number"],
            title=row["title"],
            agency=row["agency"],
            psc_code=row.get("psc_code") or None,
            posted_date=row["posted_date"],
            response_deadline=row.get("response_deadline") or None,
            description=row.get("description") or "",
            url=row.get("url") or None,
            attachments=split_list(row.get("attachments")),
            nsns=split_list(row.get("nsns")),
            part_numbers=split_list(row.get("part_numbers")),
            quantities=[int(q) for q in split_list(row.get("quantities"))],
            pricing_score=int(row.get("pricing_score") or 0),
            pricing_flags=split_list(row.get("pricing_flags")),
            fetched_at=row["fetched_at"],
            description_enriched=parse_bool(row.get("description_enriched")),
        )
