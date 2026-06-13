"""Tests for state persistence and first-writer-wins frame writing."""

from __future__ import annotations

import pytest

from screenshotter import frames
from screenshotter.state import State, read_state, state_path, write_state

# --- state ----------------------------------------------------------------------


def test_read_missing_state_returns_empty(tmp_path) -> None:
    state = read_state(state_path("E", tmp_path))
    assert state == State()
    assert state.last_image_hash is None


def test_write_then_read_roundtrip(tmp_path) -> None:
    path = state_path("E", tmp_path)
    written = State(
        last_image_hash="abc123",
        last_captured_at="2024-01-01T00:00:00+00:00",
        element_type="assembly",
        display_name="Drivetrain",
        document_name="Robot 2026",
    )
    write_state(path, written)
    assert read_state(path) == written


def test_write_creates_state_directory(tmp_path) -> None:
    path = state_path("E", tmp_path)
    assert not path.parent.exists()
    write_state(path, State(last_image_hash="h1"))
    assert path.exists()


def test_read_corrupt_state_raises(tmp_path) -> None:
    path = state_path("E", tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("not json{", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt"):
        read_state(path)


def test_read_ignores_unknown_keys(tmp_path) -> None:
    path = state_path("E", tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('{"last_image_hash": "h1", "future_field": 42}', encoding="utf-8")
    assert read_state(path).last_image_hash == "h1"


# --- frames ---------------------------------------------------------------------


def test_frame_path_layout(tmp_path) -> None:
    p = frames.frame_path("E", "2024-01-05_09", tmp_path)
    assert p == tmp_path / "frames" / "E" / "2024-01-05_09.png"


def test_write_frame_creates_and_returns_true(tmp_path) -> None:
    wrote = frames.write_frame("E", "2024-01-05_09", b"PNGDATA", tmp_path)
    assert wrote is True
    assert frames.frame_path("E", "2024-01-05_09", tmp_path).read_bytes() == b"PNGDATA"


def test_write_frame_first_writer_wins(tmp_path) -> None:
    assert frames.write_frame("E", "slot", b"first", tmp_path) is True
    # Second write must not overwrite and must report it did nothing.
    assert frames.write_frame("E", "slot", b"second", tmp_path) is False
    assert frames.frame_path("E", "slot", tmp_path).read_bytes() == b"first"


def test_exists_reflects_written_frames(tmp_path) -> None:
    assert frames.exists("E", "slot", tmp_path) is False
    frames.write_frame("E", "slot", b"x", tmp_path)
    assert frames.exists("E", "slot", tmp_path) is True


def test_list_frames_sorted_and_empty(tmp_path) -> None:
    assert frames.list_frames("E", tmp_path) == []
    for slot in ["2024-01-05_23", "2024-01-05_00", "2024-01-05_09"]:
        frames.write_frame("E", slot, b"x", tmp_path)
    names = [p.stem for p in frames.list_frames("E", tmp_path)]
    assert names == ["2024-01-05_00", "2024-01-05_09", "2024-01-05_23"]
