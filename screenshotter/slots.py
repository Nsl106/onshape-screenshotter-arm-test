"""Pure time/slot logic for the capture job. No I/O, no network — unit-testable.

Provides the canonical slot key for an instant, plus the capture-hours gate that
lets the scheduled job decide whether the current hour is one the team asked to
screenshot at (and otherwise skip, spending zero API calls).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _to_utc(t: datetime) -> datetime:
    """Normalize any datetime to timezone-aware UTC.

    A naive datetime is assumed to already be in UTC; an aware one is converted, so
    every slot key is anchored to UTC regardless of the caller's timezone.
    """
    if t.tzinfo is None:
        return t.replace(tzinfo=UTC)
    return t.astimezone(UTC)


def slot_key(t: datetime) -> str:
    """Return the canonical ``YYYY-MM-DD_HH`` slot key for instant ``t`` (UTC).

    This is the single source of truth for a frame's filename, derived from the
    run's wall-clock hour. Slot keys sort chronologically as plain strings, so the
    timelapse stitcher can order frames lexicographically.
    """
    return _to_utc(t).strftime("%Y-%m-%d_%H")


def resolve_timezone(name: str) -> tzinfo:
    """Resolve an IANA timezone name (e.g. ``"America/New_York"``) to a tzinfo.

    ``"UTC"`` is handled without consulting the system tz database so the default
    always works even on minimal images.

    Raises:
        ValueError: if the name isn't a known timezone.
    """
    if name.strip().upper() == "UTC":
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone '{name}'") from exc


def should_capture(now: datetime, tz_name: str, capture_hours: Iterable[int]) -> bool:
    """Return True if ``now``'s local hour is one the team wants a screenshot at.

    ``capture_hours`` is a set of local hours (0-23) interpreted in ``tz_name``. An
    empty collection means "take no screenshots" — capture is paused, no API calls.
    The scheduled workflow wakes hourly and calls this to decide whether to spend a
    call.
    """
    hours = set(capture_hours)
    if not hours:
        return False
    local_hour = _to_utc(now).astimezone(resolve_timezone(tz_name)).hour
    return local_hour in hours
