"""Tests for the README Tracked-CAD index generation."""

from __future__ import annotations

from screenshotter import frames, index
from screenshotter.config import Target
from screenshotter.state import State, state_path, write_state


def _target(eid: str) -> Target:
    return Target(
        url=f"https://cad.onshape.com/documents/D/w/W/e/{eid}",
        document_id="D",
        workspace_id="W",
        element_id=eid,
    )


def test_build_directory_lists_targets_with_counts(tmp_path) -> None:
    write_state(
        state_path("E1", tmp_path),
        State(display_name="Drivetrain", document_name="Robot 2026"),
    )
    frames.write_frame("E1", "2024-01-05_09", b"x", tmp_path)
    frames.write_frame("E1", "2024-01-05_10", b"x", tmp_path)
    body = index.build_directory((_target("E1"),), tmp_path)
    assert body == "- [Robot 2026 / Drivetrain](frames/E1/) — 2 frames"


def test_build_directory_falls_back_to_element_id(tmp_path) -> None:
    body = index.build_directory((_target("E1"),), tmp_path)
    assert body == "- [E1](frames/E1/) — 0 frames"


def test_build_directory_singular_frame(tmp_path) -> None:
    frames.write_frame("E1", "slot", b"x", tmp_path)
    body = index.build_directory((_target("E1"),), tmp_path)
    assert "1 frame" in body and "1 frames" not in body


def test_update_readme_replaces_only_the_section(tmp_path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(
        "# Keep me\n\n<!-- targets:start -->\nOLD\n<!-- targets:end -->\n\nFooter\n",
        encoding="utf-8",
    )
    assert index.update_readme((_target("E1"),), tmp_path) is True
    text = readme.read_text(encoding="utf-8")
    assert "# Keep me" in text and "Footer" in text
    assert "OLD" not in text
    assert "[E1](frames/E1/)" in text


def test_update_readme_no_markers_is_noop(tmp_path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("# No markers here\n", encoding="utf-8")
    assert index.update_readme((_target("E1"),), tmp_path) is False
    assert readme.read_text(encoding="utf-8") == "# No markers here\n"


def test_update_readme_missing_file_is_noop(tmp_path) -> None:
    assert index.update_readme((_target("E1"),), tmp_path) is False
