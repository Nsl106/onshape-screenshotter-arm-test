"""Timelapse stitcher — combine committed frames into a video.

Run as ``python -m screenshotter.timelapse``.

Collects ``frames/<element_id>/*.png`` in chronological order (slot keys sort by
time) and drives ffmpeg via subprocess to produce ``timelapse/<element_id>.mp4``
(and ``.gif`` with ``--gif``). It uses ffmpeg's concat demuxer with an explicit
file list, so gaps in the slot sequence — which are normal, since frames are
change-driven — are handled naturally. The command-building logic is separated
from execution so it can be unit-tested without shelling out.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import frames
from .config import Config, ConfigError, load_config

TIMELAPSE_DIR = "timelapse"

# A command runner, injectable so tests can assert on the argv without executing.
Runner = Callable[[list[str]], None]


@dataclass
class TimelapseResult:
    """Per-target stitching outcome."""

    element_id: str
    status: str
    detail: str = ""

    def line(self) -> str:
        suffix = f" {self.detail}" if self.detail else ""
        return f"{self.element_id}: {self.status}{suffix}"


def output_path(element_id: str, root: Path | str = ".", ext: str = "mp4") -> Path:
    """Return the output video path: ``<root>/timelapse/<element_id>.<ext>``."""
    return Path(root) / TIMELAPSE_DIR / f"{element_id}.{ext}"


def build_concat_list(frame_paths: list[Path], fps: int) -> str:
    """Build the ffmpeg concat-demuxer list body for the given frames.

    Each frame is shown for ``1/fps`` seconds. The last frame is repeated without a
    trailing duration, which the concat demuxer requires so the final image isn't
    dropped. Absolute POSIX paths keep the list independent of ffmpeg's CWD.
    """
    duration = 1.0 / fps
    lines: list[str] = []
    for path in frame_paths:
        lines.append(f"file '{path.resolve().as_posix()}'")
        lines.append(f"duration {duration:.6f}")
    if frame_paths:
        lines.append(f"file '{frame_paths[-1].resolve().as_posix()}'")
    return "\n".join(lines) + "\n"


def build_ffmpeg_command(
    list_path: Path, out_path: Path, fps: int, *, gif: bool = False
) -> list[str]:
    """Construct the ffmpeg argv for stitching a concat list into a video or gif.

    For mp4 we pad to even dimensions and use yuv420p so the H.264 output plays in
    browsers; for gif we apply an fps/scale filter. ``-safe 0`` allows the absolute
    paths in the concat list.
    """
    base = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
    ]
    if gif:
        vf = f"fps={fps},scale=trunc(iw/2)*2:-2:flags=lanczos"
        return [*base, "-vf", vf, str(out_path)]
    vf = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    return [*base, "-vf", vf, "-r", str(fps), "-pix_fmt", "yuv420p", str(out_path)]


def _default_runner(cmd: list[str]) -> None:
    """Run ffmpeg, raising RuntimeError with its stderr tail on failure."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = proc.stderr.strip().splitlines()[-5:]
        raise RuntimeError("ffmpeg failed:\n" + "\n".join(tail))


def stitch_target(
    element_id: str,
    fps: int,
    *,
    root: Path | str = ".",
    gif: bool = False,
    runner: Runner | None = None,
) -> TimelapseResult:
    """Stitch one target's frames into a video (and gif if requested).

    Handles the trivial cases: no frames (skipped with a message) and a single
    frame (still produces a valid short video — the concat list repeats it).
    """
    runner = runner or _default_runner
    root = Path(root)
    frame_paths = frames.list_frames(element_id, root)
    if not frame_paths:
        return TimelapseResult(element_id, "skipped", "(no frames yet)")

    out_dir = Path(root) / TIMELAPSE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    list_body = build_concat_list(frame_paths, fps)

    # The concat list is a throwaway scratch file; frames themselves are the record.
    with tempfile.NamedTemporaryFile(
        "w", suffix=".txt", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(list_body)
        list_path = Path(handle.name)
    try:
        runner(build_ffmpeg_command(list_path, output_path(element_id, root), fps))
        if gif:
            runner(
                build_ffmpeg_command(
                    list_path, output_path(element_id, root, "gif"), fps, gif=True
                )
            )
    finally:
        list_path.unlink(missing_ok=True)

    return TimelapseResult(
        element_id,
        "stitched",
        f"{len(frame_paths)} frames -> {output_path(element_id, root).name}",
    )


def run(
    config: Config,
    *,
    root: Path | str = ".",
    gif: bool = False,
    runner: Runner | None = None,
) -> list[TimelapseResult]:
    """Stitch a timelapse for every configured target; return per-target results."""
    fps = config.settings.timelapse_fps
    results: list[TimelapseResult] = []
    for target in config.targets:
        try:
            results.append(
                stitch_target(target.element_id, fps, root=root, gif=gif, runner=runner)
            )
        except Exception as exc:  # noqa: BLE001 - isolate per-target failures
            results.append(TimelapseResult(target.element_id, "error", repr(exc)))
    return results


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint. Returns 0 unless *every* target errored."""
    parser = argparse.ArgumentParser(
        prog="screenshotter.timelapse",
        description="Stitch committed frames into a timelapse video per target.",
    )
    parser.add_argument("--config", default="config.toml", help="path to config.toml")
    parser.add_argument(
        "--root", default=".", help="repo root containing frames/ and timelapse/"
    )
    parser.add_argument(
        "--gif", action="store_true", help="also render an animated .gif per target"
    )
    args = parser.parse_args(argv)

    if shutil.which("ffmpeg") is None:
        print(
            "ffmpeg was not found on PATH. Install it (Ubuntu: "
            "'sudo apt install ffmpeg', macOS: 'brew install ffmpeg'); the Timelapse "
            "workflow installs it automatically.",
            file=sys.stderr,
        )
        return 1

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    results = run(config, root=args.root, gif=args.gif)
    for result in results:
        print(result.line())

    if results and all(r.status == "error" for r in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
