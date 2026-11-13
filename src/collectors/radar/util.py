"""Small helpers shared by the collectors (time handling, ids)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

_FRACTION_RE = re.compile(r"(\.\d{6})\d+")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    """Format a datetime as an ISO-8601 UTC string with second precision ('...Z')."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: object) -> datetime | None:
    """Parse the ISO-8601 variants returned by the APIs we use. Returns aware UTC or None."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    text = _FRACTION_RE.sub(r"\1", text)  # Python accepts at most 6 fractional digits
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]
