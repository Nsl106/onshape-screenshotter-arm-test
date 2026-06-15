"""Tests for the pure slot/time logic (no I/O, no network)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from screenshotter.slots import due_capture_slot, resolve_timezone, slot_key


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


# --- due_capture_slot -----------------------------------------------------------


def test_due_slot_at_a_capture_hour() -> None:
    # Exactly on 12:00 -> the 12 slot is due.
    assert due_capture_slot(_utc(2024, 1, 5, 12), "UTC", [8, 12, 16]) == "2024-01-05:12"


def test_due_slot_catches_up_a_missed_hour() -> None:
    # No run fired at 08:00; the next run at 10:xx still owes the 08 slot.
    assert due_capture_slot(_utc(2024, 1, 5, 10), "UTC", [8, 12, 16]) == "2024-01-05:08"
    # Late single run sees only the latest due hour, not every earlier one.
    assert due_capture_slot(_utc(2024, 1, 5, 17), "UTC", [8, 12, 16]) == "2024-01-05:16"


def test_due_slot_none_before_first_hour() -> None:
    assert due_capture_slot(_utc(2024, 1, 5, 6), "UTC", [8, 12, 16]) is None


def test_due_slot_none_when_paused() -> None:
    assert due_capture_slot(_utc(2024, 1, 5, 17), "UTC", []) is None


def test_due_slot_respects_timezone_and_local_date() -> None:
    # 02:00 UTC on the 6th is 21:00 Eastern on the 5th; with a 20:00 capture hour
    # the due slot is dated by the *local* day.
    due = due_capture_slot(_utc(2024, 1, 6, 2), "America/New_York", [20])
    assert due == "2024-01-05:20"
