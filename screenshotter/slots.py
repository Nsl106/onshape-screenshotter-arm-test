"""Pure time/slot logic for the capture job. No I/O, no network — unit-testable.

Provides the canonical slot key for an instant, plus the capture-hours gate: it
tells the scheduled job which capture is currently due (catching up a missed hour
on the next run), so it knows whether to spend an API call or skip.
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


def due_capture_slot(
    now: datetime, tz_name: str, capture_hours: Iterable[int]
) -> str | None:
    """Return an id for the capture that is due as of ``now``, or None if none is.

    ``capture_hours`` are local hours (0-23) in ``tz_name``. The returned id is
    ``"<local-date>:<hour>"`` for the *latest* capture hour that has already arrived
    today (e.g. ``"2026-06-15:16"``). The caller records the id it last serviced, so
    each capture hour is fulfilled once per day by the first run at or after it —
    even if GitHub's scheduler drops the run that fell exactly in that hour. This
    catch-up is what keeps a flaky hourly cron from silently missing screenshots.

    Returns None when capture is paused (empty list) or no capture hour has arrived
    yet today.
    """
    hours = sorted(set(capture_hours))
    if not hours:
        return None
    local = _to_utc(now).astimezone(resolve_timezone(tz_name))
    arrived = [h for h in hours if h <= local.hour]
    if not arrived:
        return None
    return f"{local.date().isoformat()}:{max(arrived):02d}"
