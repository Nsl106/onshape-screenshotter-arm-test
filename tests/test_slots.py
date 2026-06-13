"""Tests for the pure slot/time logic (no I/O, no network)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from screenshotter.slots import resolve_timezone, should_capture, slot_key


def _utc(y, mo, d, h=0, mi=0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


# --- slot_key -------------------------------------------------------------------


def test_slot_key_format() -> None:
    assert slot_key(_utc(2024, 1, 5, 9, 30)) == "2024-01-05_09"


def test_slot_key_normalizes_other_timezone_to_utc() -> None:
    plus2 = timezone(timedelta(hours=2))
    t = datetime(2024, 1, 5, 9, 30, tzinfo=plus2)
    assert slot_key(t) == "2024-01-05_07"


def test_slot_key_treats_naive_as_utc() -> None:
    assert slot_key(datetime(2024, 1, 5, 9, 30)) == "2024-01-05_09"


def test_slot_keys_sort_chronologically() -> None:
    keys = [slot_key(_utc(2024, 1, 5, h)) for h in (23, 0, 9)]
    assert sorted(keys) == ["2024-01-05_00", "2024-01-05_09", "2024-01-05_23"]


# --- resolve_timezone -----------------------------------------------------------


def test_resolve_utc() -> None:
    assert resolve_timezone("UTC") is UTC
    assert resolve_timezone("utc") is UTC


def test_resolve_named_zone() -> None:
    # A real zone resolves and applies the expected offset.
    tz = resolve_timezone("America/New_York")
    # 2024-01-05 12:00 UTC is 07:00 EST.
    assert _utc(2024, 1, 5, 12).astimezone(tz).hour == 7


def test_resolve_bad_zone_raises() -> None:
    with pytest.raises(ValueError, match="unknown timezone"):
        resolve_timezone("Mars/Olympus_Mons")


# --- should_capture -------------------------------------------------------------


def test_capture_only_at_listed_hours() -> None:
    hours = [8, 12, 16, 20]
    assert should_capture(_utc(2024, 1, 5, 12), "UTC", hours) is True
    assert should_capture(_utc(2024, 1, 5, 8), "UTC", hours) is True
    assert should_capture(_utc(2024, 1, 5, 13), "UTC", hours) is False
    assert should_capture(_utc(2024, 1, 5, 0), "UTC", hours) is False


def test_empty_capture_hours_means_never() -> None:
    # Empty list pauses capture entirely — no screenshot at any hour.
    assert should_capture(_utc(2024, 1, 5, 3), "UTC", []) is False
    assert should_capture(_utc(2024, 1, 5, 17), "UTC", ()) is False


def test_capture_hours_respect_timezone() -> None:
    # 13:00 UTC is 08:00 in New York; an 08:00-Eastern capture hour should fire.
    assert should_capture(_utc(2024, 1, 5, 13), "America/New_York", [8]) is True
    # 12:00 UTC is 07:00 Eastern -> not a capture hour.
    assert should_capture(_utc(2024, 1, 5, 12), "America/New_York", [8]) is False
