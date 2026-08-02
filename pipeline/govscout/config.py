"""JSON config loading/saving for GovScout."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

DEFAULT_CONFIG_NAME = "config.json"

# Monday=0 .. Sunday=6, matching date.weekday().
WEEKDAY_KEYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# Default coverage-ledger tracked codes: FSC/PSC classes for the three
# high-volume, NSN-dense product families discussed for the initial backfill
# — hardware/fasteners, electrical/electronic components, and vehicular/
# engine parts. These are standard DLA/GSA Federal Supply Classification
# groups (53xx, 59xx, 25xx/28xx/29xx) and mostly overlap the codes already
# validated in the legacy psc_rotation below. Not a final answer — a
# one-line edit to this list (or to a project config.json) changes what gets
# tracked.
DEFAULT_TRACKED_CODES: tuple[str, ...] = (
    # Hardware & fasteners (FSC group 53)
    "5305",  # Screws
    "5306",  # Bolts
    "5307",  # Studs
    "5310",  # Nuts and Washers
    "5315",  # Nails, Keys, and Pins
    "5320",  # Rivets
    "5325",  # Fastening Devices
    "5340",  # Hardware, Miscellaneous
    # Electrical & electronic components (FSC group 59)
    "5905",  # Resistors
    "5910",  # Capacitors
    "5920",  # Fuses and Lightning Arresters
    "5925",  # Circuit Breakers
    "5930",  # Switches
    "5935",  # Connectors
    "5945",  # Relays and Solenoids
    "5961",  # Semiconductor Devices and Associated Hardware
    "5962",  # Microcircuits, Electronic
    "5998",  # Electrical/Electronic Assemblies, Boards, Cards
    # Vehicular & engine parts (FSC groups 25/28/29)
    "2510",  # Vehicular Cab, Body, and Frame Structural Components
    "2520",  # Vehicular Power Transmission Components
    "2530",  # Vehicular Brake, Steering, Axle, and Wheel Components
    "2805",  # Gasoline Reciprocating Engines
    "2815",  # Diesel Engines
    "2910",  # Engine Fuel System Components
    "2920",  # Engine Electrical System Components
    "2930",  # Engine Cooling System Components
    "2940",  # Engine Air, Oil, and Fuel Filters
)


@dataclass
class Config:
    """Runtime configuration.

    psc_codes: PSC/FSC codes to filter on (empty = no filter). Used by demo
        mode and as the fallback for live mode when psc_rotation is empty.
    psc_rotation: optional weekday -> PSC/FSC codes map ("mon".."sun") for the
        free-tier rotation strategy. When non-empty, live fetches use only
        today's group instead of psc_codes — see todays_psc_codes() and the
        "Free-tier PSC rotation" section in README.md. With an entity-role
        API key (~1000 req/day) this can be left empty to query psc_codes
        (or a single big list) every day instead.
    days_back: how far back to search in live mode.
    db_path: SQLite database location.
    output_dir: default directory for CSV exports.
    max_pages: SAM.gov pages (100 records each) to fetch per live run, used
        by the older weekday-rotation `fetch` command. Default 1 keeps a run
        to a single request — personal api.data.gov keys without an entity
        role are capped at 10 requests/day (confirmed), so one page per run
        stays well inside budget. An entity-role key is expected to raise
        the quota to ~1000/day (unconfirmed estimate); raise this once you
        have one.
    tracked_codes: PSC/FSC codes for the coverage ledger (backfill + ongoing
        refresh via `python -m govscout sync`) — see DEFAULT_TRACKED_CODES
        for the default and its rationale. Independent of psc_codes/
        psc_rotation, which only the older weekday-rotation `fetch` command
        uses.
    lookback_months: how many calendar months back (inclusive of the
        current month) the coverage ledger's slice universe covers, per
        tracked code. Default 12.
    ledger_path: coverage ledger CSV path — committed to the repo (unlike
        db_path), since it tracks state (which slices have never been
        fetched) that the fetched data itself can't reconstruct.
    sync_max_pages: SAM.gov pages per slice for `sync`, separate from
        max_pages (which the broader weekday-rotation fetch uses). A slice
        is one code x one month, much narrower than the rotation's
        multi-code x days_back query, so 1 page is almost always enough;
        default 1 also bounds a single slice's worst-case cost to exactly 1
        request against the confirmed 10/day cap.
    default_slices_per_run: how many slices `sync` processes when not
        overridden by --slices/workflow_dispatch. Default 5: with
        sync_max_pages=1 that's <=5 of the 10 confirmed daily requests,
        leaving headroom for the scheduled `fetch` step (<=max_pages
        requests) on days both run, plus a manual retry. Tune once you're
        actually running this — see sources/samgov.py's RATE_LIMIT_MSG.
    """

    psc_codes: list[str] = field(default_factory=list)
    psc_rotation: dict[str, list[str]] = field(default_factory=dict)
    days_back: int = 30
    db_path: str = "govscout.db"
    output_dir: str = "out"
    max_pages: int = 1
    tracked_codes: list[str] = field(default_factory=lambda: list(DEFAULT_TRACKED_CODES))
    lookback_months: int = 12
    ledger_path: str = "coverage_ledger.csv"
    sync_max_pages: int = 1
    default_slices_per_run: int = 5

    def todays_psc_codes(self, today: date | None = None) -> list[str]:
        """PSC/FSC codes to query for ``today`` (UTC by default).

        Returns today's weekday group from ``psc_rotation`` if configured,
        otherwise falls back to the plain ``psc_codes`` list.
        """
        if not self.psc_rotation:
            return list(self.psc_codes)
        today = today or datetime.now(timezone.utc).date()
        key = WEEKDAY_KEYS[today.weekday()]
        return list(self.psc_rotation.get(key, []))


def load_config(path: str | Path | None = None) -> Config:
    """Load config from JSON.

    If ``path`` is None, ``config.json`` in the current directory is used
    when present; otherwise built-in defaults are returned.
    """
    if path is None:
        candidate = Path(DEFAULT_CONFIG_NAME)
        if not candidate.exists():
            return Config()
        path = candidate
    with Path(path).open(encoding="utf-8") as fh:
        data = json.load(fh)
    known = {f for f in Config.__dataclass_fields__}
    return Config(**{k: v for k, v in data.items() if k in known})


def save_config(config: Config, path: str | Path) -> Path:
    """Write config as pretty-printed JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(asdict(config), fh, indent=2)
        fh.write("\n")
    return path
