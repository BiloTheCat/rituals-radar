"""Small helpers shared by all collectors (time handling, ids, hashing)."""

from __future__ import annotations

import hashlib
import os
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


def pseudonymize(source: str, author: str | None) -> str | None:
    """Replace an author handle/id with a salted hash.

    We never need to know *who* posted something, only how many distinct authors
    there are. Set RADAR_HASH_SALT (e.g. as a GitHub secret) so hashes cannot be
    reversed by hashing a list of known handles.
    """
    if not author:
        return None
    salt = os.environ.get("RADAR_HASH_SALT") or "rituals-radar"  # unset GitHub secrets arrive as ""
    digest = hashlib.sha256(f"{salt}|{source}|{author}".encode("utf-8")).hexdigest()
    return digest[:16]


def truncate(text: str | None, max_chars: int) -> str | None:
    if text is None:
        return None
    text = text.strip()
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"


def to_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        try:
            return int(float(value))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
