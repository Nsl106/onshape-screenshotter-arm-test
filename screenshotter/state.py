"""Per-target change-detection state: ``state/<element_id>.json``.

Each target keeps a small JSON file recording the fingerprint of the last frame it
saved (so a run can skip writing a duplicate when the CAD hasn't changed) plus
cached metadata that isn't in the pasted URL — the element's type and display name
— so we don't re-fetch it from the API every run, and the README index has names to
show even between runs.

No network access; pure filesystem I/O. A missing file is the normal first-run
case and yields an empty ``State``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class State:
    """The persisted per-target state.

    Attributes:
        last_image_hash: Fingerprint of the most recently saved frame, or None on
            first run. A run compares the freshly rendered image against this to
            decide whether the CAD changed.
        last_captured_at: ISO-8601 UTC timestamp of the last capture (for humans).
        element_type: Cached ``"assembly"``/``"partstudio"`` from element metadata.
        display_name: Cached element (tab) name for the README index.
        document_name: Cached document name for extra context in the README index.
    """

    last_image_hash: str | None = None
    last_captured_at: str | None = None
    element_type: str | None = None
    display_name: str | None = None
    document_name: str | None = None


def state_path(element_id: str, root: Path | str = ".") -> Path:
    """Return the state-file path for a target: ``<root>/state/<element_id>.json``."""
    return Path(root) / "state" / f"{element_id}.json"


def read_state(path: Path | str) -> State:
    """Read a target's state file, returning an empty ``State`` if it doesn't exist.

    A missing file is the expected first-run condition. A present-but-unreadable
    file raises, since silently discarding real state could cause re-capture or an
    incorrect handoff.

    Raises:
        ValueError: if the file exists but isn't valid JSON / the expected shape.
    """
    p = Path(path)
    if not p.exists():
        return State()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"State file '{p}' is corrupt: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"State file '{p}' is not a JSON object.")
    # Ignore unknown keys so a newer file can be read by older code and vice versa.
    known = {f for f in State.__dataclass_fields__}
    return State(**{k: v for k, v in data.items() if k in known})


def write_state(path: Path | str, state: State) -> None:
    """Write a target's state file as pretty JSON, creating ``state/`` if needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(state), indent=2, sort_keys=True) + "\n", "utf-8")
