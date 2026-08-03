"""SQLite persistence with dedupe-by-solicitation-number."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import ROW_FIELDS, Solicitation

_SCHEMA = """
CREATE TABLE IF NOT EXISTS solicitations (
    sol_number       TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    agency           TEXT NOT NULL,
    psc_code         TEXT,
    posted_date      TEXT,
    response_deadline TEXT,
    url              TEXT,
    attachments      TEXT,
    nsns             TEXT,
    part_numbers     TEXT,
    quantities       TEXT,
    pricing_score    INTEGER,
    pricing_flags    TEXT,
    description      TEXT,
    description_enriched INTEGER,
    fetched_at       TEXT,
    first_seen       TEXT NOT NULL
);
"""


class Store:
    """SQLite-backed store. Upserts on sol_number conflict (dedupe)."""

    def __init__(self, path: str | Path = "govscout.db") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------- mutation

    def upsert(self, sol: Solicitation) -> bool:
        """Insert or update a solicitation. Returns True if it was newly inserted."""
        row = sol.to_row()
        existing = self._conn.execute(
            "SELECT 1 FROM solicitations WHERE sol_number = ?", (sol.sol_number,)
        ).fetchone()
        if existing:
            assignments = ", ".join(f"{field} = ?" for field in ROW_FIELDS)
            self._conn.execute(
                f"UPDATE solicitations SET {assignments} WHERE sol_number = ?",
                [row[field] for field in ROW_FIELDS] + [sol.sol_number],
            )
            self._conn.commit()
            return False
        columns = ", ".join(ROW_FIELDS) + ", first_seen"
        placeholders = ", ".join("?" for _ in ROW_FIELDS) + ", ?"
        first_seen = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        self._conn.execute(
            f"INSERT INTO solicitations ({columns}) VALUES ({placeholders})",
            [row[field] for field in ROW_FIELDS] + [first_seen],
        )
        self._conn.commit()
        return True

    def upsert_many(self, sols: list[Solicitation]) -> tuple[int, int]:
        """Upsert a batch. Returns (new_count, updated_count)."""
        new = updated = 0
        for sol in sols:
            if self.upsert(sol):
                new += 1
            else:
                updated += 1
        return new, updated

    # --------------------------------------------------------------- query

    def count(self) -> int:
        """Total number of stored solicitations."""
        return self._conn.execute("SELECT COUNT(*) FROM solicitations").fetchone()[0]

    def get_description(self, sol_number: str) -> str | None:
        """Previously-stored description for ``sol_number``, or None if unseen.

        Used to cache description enrichment across runs (see describe.py):
        a notice already holding real narrative text from an earlier fetch
        is never dereferenced again.
        """
        row = self._conn.execute(
            "SELECT description FROM solicitations WHERE sol_number = ?", (sol_number,)
        ).fetchone()
        return row["description"] if row else None

    def all(self) -> list[Solicitation]:
        """All solicitations, best pricing signals first."""
        cursor = self._conn.execute(
            "SELECT * FROM solicitations ORDER BY pricing_score DESC, posted_date DESC, sol_number"
        )
        return [Solicitation.from_row(dict(row)) for row in cursor.fetchall()]

    def new_since(self, iso_timestamp: str) -> list[Solicitation]:
        """Solicitations first seen at or after the given ISO timestamp."""
        cursor = self._conn.execute(
            "SELECT * FROM solicitations WHERE first_seen >= ? "
            "ORDER BY pricing_score DESC, posted_date DESC, sol_number",
            (iso_timestamp,),
        )
        return [Solicitation.from_row(dict(row)) for row in cursor.fetchall()]

    # -------------------------------------------------------------- lifecycle

    def close(self) -> None:
        """Close the underlying database connection."""
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
