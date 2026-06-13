"""Tests for timelapse command building and stitching (ffmpeg is not executed)."""

from __future__ import annotations

from pathlib import Path

from screenshotter import frames, timelapse
from screenshotter.config import Config, Settings, Target


def _config(fps=10, *targets: Target) -> Config:
    settings = Settings(
        image_width=64,
        image_height=64,
        view="isometric",
        timelapse_fps=fps,
        keepalive=True,
        timezone="UTC",
        capture_hours=(),
    )
    target = Target(
        url="https://cad.onshape.com/documents/D/w/W/e/E1",
        document_id="D",
        workspace_id="W",
        element_id="E1",
    )
    return Config(settings=settings, targets=targets or (target,))


# --- concat list ----------------------------------------------------------------


def test_concat_list_has_duration_and_repeats_last(tmp_path) -> None:
    paths = [tmp_path / "a.png", tmp_path / "b.png"]
    for p in paths:
        p.write_bytes(b"x")
    body = build = timelapse.build_concat_list(paths, fps=10)
    assert build.count("file '") == 3  # 2 frames + repeated last
    assert "duration 0.100000" in body
    # Last line repeats the final frame without a trailing duration.
    assert body.strip().splitlines()[-1] == f"file '{paths[-1].resolve().as_posix()}'"


def test_concat_list_single_frame(tmp_path) -> None:
    p = tmp_path / "only.png"
    p.write_bytes(b"x")
    body = timelapse.build_concat_list([p], fps=5)
    assert body.count("file '") == 2  # the one frame, then repeated
    assert "duration 0.200000" in body


# --- ffmpeg command -------------------------------------------------------------


def test_mp4_command_shape() -> None:
    cmd = timelapse.build_ffmpeg_command(
        Path("/tmp/list.txt"), Path("/out/E1.mp4"), fps=12
    )
    assert cmd[0] == "ffmpeg"
    assert "-f" in cmd and "concat" in cmd
    assert "-safe" in cmd and "0" in cmd
    assert cmd[cmd.index("-i") + 1] == "/tmp/list.txt"
    assert cmd[cmd.index("-r") + 1] == "12"
    assert "yuv420p" in cmd
    assert cmd[-1] == "/out/E1.mp4"


def test_gif_command_uses_fps_filter() -> None:
    cmd = timelapse.build_ffmpeg_command(
        Path("/tmp/list.txt"), Path("/out/E1.gif"), fps=8, gif=True
    )
    vf = cmd[cmd.index("-vf") + 1]
    assert "fps=8" in vf
    assert cmd[-1] == "/out/E1.gif"


# --- stitch_target --------------------------------------------------------------


def test_stitch_no_frames_skips(tmp_path) -> None:
    calls: list[list[str]] = []
    result = timelapse.stitch_target("E1", 10, root=tmp_path, runner=calls.append)
    assert result.status == "skipped"
    assert calls == []  # ffmpeg never invoked


def test_stitch_invokes_runner_with_output(tmp_path) -> None:
    for slot in ["2024-01-01_00", "2024-01-02_00"]:
        frames.write_frame("E1", slot, b"x", tmp_path)
    calls: list[list[str]] = []
    result = timelapse.stitch_target("E1", 10, root=tmp_path, runner=calls.append)
    assert result.status == "stitched"
    assert len(calls) == 1  # mp4 only (no gif)
    assert calls[0][-1] == str(timelapse.output_path("E1", tmp_path))


def test_stitch_gif_invokes_runner_twice(tmp_path) -> None:
    frames.write_frame("E1", "2024-01-01_00", b"x", tmp_path)
    calls: list[list[str]] = []
    timelapse.stitch_target("E1", 10, root=tmp_path, gif=True, runner=calls.append)
    assert len(calls) == 2
    assert calls[1][-1].endswith("E1.gif")


def test_stitch_cleans_up_temp_list(tmp_path) -> None:
    frames.write_frame("E1", "2024-01-01_00", b"x", tmp_path)
    seen_list_paths: list[str] = []

    def runner(cmd: list[str]) -> None:
        seen_list_paths.append(cmd[cmd.index("-i") + 1])

    timelapse.stitch_target("E1", 10, root=tmp_path, runner=runner)
    # The concat list is a temp file that must be removed after stitching.
    assert not Path(seen_list_paths[0]).exists()


def test_run_isolates_target_errors(tmp_path) -> None:
    frames.write_frame("E1", "2024-01-01_00", b"x", tmp_path)

    def boom(cmd: list[str]) -> None:
        raise RuntimeError("ffmpeg exploded")

    [result] = timelapse.run(_config(), root=tmp_path, runner=boom)
    assert result.status == "error"
    assert "ffmpeg exploded" in result.detail
