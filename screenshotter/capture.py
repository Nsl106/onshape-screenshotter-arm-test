"""Forward capture job — the scheduled entrypoint (``python -m screenshotter.capture``).

Each run renders every configured target's current workspace state in a single API
call, then decides locally whether to keep the frame: if the rendered image matches
the last one saved (same fingerprint), the CAD didn't change and nothing is written.
So an unchanged run and a changed run both cost exactly one render call — the
cheapest the tool can be against Onshape's tight annual quota. Runs whose hour
isn't one of the configured capture hours are skipped before any API call at all.

Targets are processed independently: one failing target never stops the others. Git
commit/push is handled by the workflow, not here, so this script's only side effects
are writing into ``frames/`` / ``state/`` and refreshing the README index — making
it safe to run and ``--dry-run`` locally.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import frames, index
from .config import Config, ConfigError, Target, load_config
from .onshape import OnshapeAuthError, OnshapeClient, OnshapeError
from .slots import should_capture, slot_key
from .state import State, read_state, state_path, write_state

# Per-target outcomes, in the order they're reported on the one-line summary.
CAPTURED = "captured"
UNCHANGED = "unchanged"
SLOT_FILLED = "skipped (slot filled)"
OFF_HOURS = "skipped (not a capture hour)"
ERROR = "error"


@dataclass
class TargetResult:
    """The outcome of processing one target, for logging and the exit code."""

    element_id: str
    status: str
    detail: str = ""

    def line(self) -> str:
        """Format the concise one-line-per-target log entry."""
        suffix = f" {self.detail}" if self.detail else ""
        return f"{self.element_id}: {self.status}{suffix}"


def _ensure_metadata(client: OnshapeClient, target: Target, state: State) -> None:
    """Populate ``state`` with element type/name (and document name) if not cached.

    Onshape's annual API quota is tight (e.g. 2,500/yr on Education plans), so this
    metadata is fetched exactly once per target — on the first capture — and reused
    forever after. The display name therefore won't follow a later tab rename; that
    cosmetic staleness is a deliberate trade for spending no quota on every run.
    """
    if not state.element_type:
        meta = client.get_element_metadata(target)
        state.element_type = meta.element_type
        state.display_name = meta.name
    if not state.document_name:
        try:
            state.document_name = client.get_document_name(target.document_id)
        except OnshapeError:
            pass  # Document name is cosmetic; don't fail a capture over it.


def _process_target(
    client: OnshapeClient,
    target: Target,
    view: str,
    width: int,
    height: int,
    now: datetime,
    root: Path,
    dry_run: bool,
) -> TargetResult:
    """Render one target's current state and save it iff it changed since last time.

    Spends a single render call, fingerprints the result, and writes a new frame
    only when the fingerprint differs from the last saved one and the slot for this
    hour isn't already filled (first-writer-wins).
    """
    eid = target.element_id
    state = read_state(state_path(eid, root))
    _ensure_metadata(client, target, state)

    png = client.render_shaded_view(
        target, state.element_type, view=view, width=width, height=height
    )
    fingerprint = frames.image_fingerprint(png)
    if fingerprint == state.last_image_hash:
        return TargetResult(eid, UNCHANGED)

    slot = slot_key(now)
    if frames.exists(eid, slot, root):
        return TargetResult(eid, SLOT_FILLED, slot)

    if dry_run:
        return TargetResult(eid, CAPTURED, f"{slot} (dry-run, not written)")

    if not frames.write_frame(eid, slot, png, root):
        # Another writer filled the slot between the check and now — respect it.
        return TargetResult(eid, SLOT_FILLED, slot)

    state.last_image_hash = fingerprint
    state.last_captured_at = now.isoformat()
    write_state(state_path(eid, root), state)
    return TargetResult(eid, CAPTURED, slot)


def run(
    config: Config,
    client: OnshapeClient,
    *,
    now: datetime | None = None,
    root: Path | str = ".",
    dry_run: bool = False,
) -> list[TargetResult]:
    """Process every target as of ``now`` and return per-target results.

    If ``now``'s local hour isn't one of the configured capture hours, every target
    is skipped without any API call. Otherwise each target is isolated in its own
    try/except so one failure can't abort the rest, and the README index is
    refreshed once at the end (unless this is a dry run with no captures).
    """
    root = Path(root)
    now = now or datetime.now(UTC)
    settings = config.settings

    if not should_capture(now, settings.timezone, settings.capture_hours):
        return [TargetResult(t.element_id, OFF_HOURS) for t in config.targets]

    results: list[TargetResult] = []
    for target in config.targets:
        try:
            results.append(
                _process_target(
                    client,
                    target,
                    settings.view,
                    settings.image_width,
                    settings.image_height,
                    now,
                    root,
                    dry_run,
                )
            )
        except OnshapeError as exc:
            results.append(TargetResult(target.element_id, ERROR, str(exc)))
        except Exception as exc:  # noqa: BLE001 - isolate any target-specific failure
            results.append(TargetResult(target.element_id, ERROR, repr(exc)))

    if not dry_run and any(r.status == CAPTURED for r in results):
        index.update_readme(config.targets, root)
    return results


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint. Returns 0 unless *every* target errored."""
    parser = argparse.ArgumentParser(
        prog="screenshotter.capture",
        description="Render each configured Onshape target's current state and save "
        "it as a timelapse frame when the model has changed.",
    )
    parser.add_argument("--config", default="config.toml", help="path to config.toml")
    parser.add_argument(
        "--root", default=".", help="repo root containing frames/ and state/"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="render and report what would be captured without writing any files",
    )
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        client = OnshapeClient.from_env()
    except (ConfigError, OnshapeAuthError) as exc:
        print(exc, file=sys.stderr)
        return 1

    results = run(config, client, root=args.root, dry_run=args.dry_run)
    for result in results:
        print(result.line())

    if results and all(r.status == ERROR for r in results):
        print("All targets failed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
