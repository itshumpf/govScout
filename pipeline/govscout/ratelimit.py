"""Cross-run rate-limit backoff.

A GitHub Actions run starts from a fresh checkout every time, so without
something committed to the repo, a run has no memory of a previous run's
429 — a human re-clicking "run workflow" a few times in a row (or the
schedule firing on top of manual testing) burns real requests just to
re-discover the same rate limit each time. This module persists the
timestamp of the last confirmed 429 so callers can skip straight to "we
already know we're cooling down" without spending another request.

The 24h window (rather than "until UTC midnight") is deliberate: SAM.gov
doesn't publicly document exactly when/how the daily quota resets, so a
fixed 24h-from-the-429 backoff is the conservative choice given that
uncertainty — see sources/samgov.py's RATE_LIMIT_MSG.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

COOLDOWN = timedelta(hours=24)


@dataclass
class RateLimitState:
    last_429_at: datetime | None = None

    def cooling_down(self, now: datetime | None = None) -> bool:
        if self.last_429_at is None:
            return False
        now = now or datetime.now(timezone.utc)
        return now - self.last_429_at < COOLDOWN

    def retry_after(self) -> datetime | None:
        if self.last_429_at is None:
            return None
        return self.last_429_at + COOLDOWN


def load(path: str | Path) -> RateLimitState:
    """Load state from ``path``, or an empty (not-cooling-down) state if missing/corrupt."""
    path = Path(path)
    if not path.exists():
        return RateLimitState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("last_429_at")
        return RateLimitState(last_429_at=datetime.fromisoformat(raw) if raw else None)
    except (json.JSONDecodeError, OSError, ValueError):
        return RateLimitState()


def record_429(path: str | Path, when: datetime | None = None) -> None:
    """Persist that a real 429 just happened — committed to the repo by the workflow."""
    when = when or datetime.now(timezone.utc)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"last_429_at": when.isoformat()}) + "\n", encoding="utf-8")


def clear(path: str | Path) -> None:
    """Drop the marker — called after a live call succeeds, so the file's mere
    presence always means "there's an active cooldown" rather than a stale one."""
    path = Path(path)
    if path.exists():
        path.unlink()
