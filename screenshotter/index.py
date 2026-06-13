"""Maintain the auto-generated "Tracked CAD" directory in the README.

Both jobs call ``update_readme`` after writing frames. It rewrites the block
between the ``<!-- targets:start -->`` and ``<!-- targets:end -->`` markers with one
entry per configured target — its fetched display name (from the cached state)
linked to its ``frames/<element_id>/`` folder, plus the current frame count — so the
team gets a human-readable gallery index without hand-editing anything.
"""

from __future__ import annotations

from pathlib import Path

from . import frames
from .config import Target
from .state import read_state, state_path

START_MARKER = "<!-- targets:start -->"
END_MARKER = "<!-- targets:end -->"

_EMPTY_MESSAGE = "_No frames captured yet. Run the Capture workflow to populate this._"


def _label(display_name: str | None, document_name: str | None, element_id: str) -> str:
    """Build the human-friendly link text for one target.

    Uses ``Document / Element`` when both names are known and differ, the element
    name alone when that's all we have, and the element id as a last resort (before
    any successful metadata fetch).
    """
    name = display_name or element_id
    if document_name and document_name != name:
        return f"{document_name} / {name}"
    return name


def build_directory(targets: tuple[Target, ...], root: Path | str = ".") -> str:
    """Return the markdown body for the Tracked CAD section.

    Reads each target's cached state for its display name and counts the PNGs in
    its frame directory. Targets with no frames yet are still listed (count 0) so
    the team can see the tool recognizes them.
    """
    if not targets:
        return _EMPTY_MESSAGE
    lines: list[str] = []
    for target in targets:
        state = read_state(state_path(target.element_id, root))
        count = len(frames.list_frames(target.element_id, root))
        label = _label(state.display_name, state.document_name, target.element_id)
        plural = "frame" if count == 1 else "frames"
        lines.append(f"- [{label}](frames/{target.element_id}/) — {count} {plural}")
    return "\n".join(lines)


def render_section(targets: tuple[Target, ...], root: Path | str = ".") -> str:
    """Return the full marker-delimited block (markers included)."""
    return f"{START_MARKER}\n{build_directory(targets, root)}\n{END_MARKER}"


def update_readme(targets: tuple[Target, ...], root: Path | str = ".") -> bool:
    """Rewrite the Tracked CAD section of ``<root>/README.md`` in place.

    Returns:
        True if the README existed, contained both markers, and was updated;
        False if the README or its markers are missing (left untouched).
    """
    readme = Path(root) / "README.md"
    if not readme.exists():
        return False
    text = readme.read_text(encoding="utf-8")
    if START_MARKER not in text or END_MARKER not in text:
        return False
    before, _, rest = text.partition(START_MARKER)
    _, _, after = rest.partition(END_MARKER)
    new_text = before + render_section(targets, root) + after
    if new_text != text:
        readme.write_text(new_text, encoding="utf-8")
    return True
