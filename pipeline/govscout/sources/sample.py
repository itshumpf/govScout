"""Bundled sample-data source for offline demo mode.

Loads realistic fake solicitations from ``data/sample_opportunities.json``
through the same Source interface as the live SAM.gov client, so demo mode
exercises the entire pipeline (fetch -> normalize -> store -> report)
without credentials or network access.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..models import Solicitation
from .base import Source, enrich_solicitation

_DEFAULT_DATA = Path(__file__).resolve().parent.parent / "data" / "sample_opportunities.json"


class SampleSource(Source):
    """Offline source backed by the bundled sample JSON file.

    Note: ``days_back`` is accepted for interface compatibility but ignored —
    the bundled records are static historical fixtures. PSC filtering is
    applied so the demo exercises the same filtering path as live mode.
    """

    name = "sample"

    def __init__(self, data_path: str | Path | None = None) -> None:
        self.data_path = Path(data_path) if data_path else _DEFAULT_DATA

    def fetch(self, psc_codes: list[str], days_back: int) -> list[dict]:
        """Load bundled records, optionally filtered to the given PSC/FSC codes."""
        with self.data_path.open(encoding="utf-8") as fh:
            records: list[dict] = json.load(fh)
        if psc_codes:
            wanted = {code.strip() for code in psc_codes}
            records = [r for r in records if (r.get("psc_code") or "") in wanted]
        return records

    def normalize(self, raw: dict) -> Solicitation:
        """Normalize a sample record into an enriched Solicitation."""
        return enrich_solicitation(raw)
