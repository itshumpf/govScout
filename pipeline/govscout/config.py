"""JSON config loading/saving for GovScout."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_CONFIG_NAME = "config.json"


@dataclass
class Config:
    """Runtime configuration.

    psc_codes: PSC/FSC codes to filter on (empty = no filter).
    days_back: how far back to search in live mode.
    db_path: SQLite database location.
    output_dir: default directory for CSV exports.
    max_pages: SAM.gov pages (100 records each) to fetch per live run. Default
        1 keeps a run to a single request — personal api.data.gov keys without
        an entity role are capped at ~10 requests/day, so one page per run
        stays well inside budget. An entity-role key raises the quota to
        ~1000/day; raise this once you have one.
    """

    psc_codes: list[str] = field(default_factory=list)
    days_back: int = 30
    db_path: str = "govscout.db"
    output_dir: str = "out"
    max_pages: int = 1


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
