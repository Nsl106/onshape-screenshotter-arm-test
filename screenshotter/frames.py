"""Frame file layout and first-writer-wins image writing.

The committed ``frames/<element_id>/`` directory is the authoritative record of
which slots are filled. Overlap between the capture job and re-runs is prevented at
the slot level: before rendering, a caller checks ``exists`` for the slot, and
``write_frame`` refuses to overwrite — so whoever writes a slot first wins and no
API quota is spent re-rendering it. No network access.
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def image_fingerprint(data: bytes) -> str:
    """Return a stable content hash of a PNG, ignoring non-pixel metadata.

    Change detection renders the current model each run and compares this
    fingerprint to the last saved frame's: equal means the CAD didn't change, so no
    new frame is written. To keep that comparison from tripping on cosmetic
    differences (an embedded timestamp or other ancillary chunk), the hash covers
    only the image-defining ``IHDR`` and ``IDAT`` chunks. Anything that isn't a
    parseable PNG falls back to hashing the raw bytes.
    """
    if not data.startswith(_PNG_SIGNATURE):
        return hashlib.sha256(data).hexdigest()
    digest = hashlib.sha256()
    offset = len(_PNG_SIGNATURE)
    try:
        while offset + 8 <= len(data):
            (length,) = struct.unpack(">I", data[offset : offset + 4])
            ctype = data[offset + 4 : offset + 8]
            body = data[offset + 8 : offset + 8 + length]
            if ctype in (b"IHDR", b"IDAT"):
                digest.update(ctype)
                digest.update(body)
            offset += 12 + length  # 4 length + 4 type + data + 4 CRC
            if ctype == b"IEND":
                break
    except struct.error:
        return hashlib.sha256(data).hexdigest()
    return digest.hexdigest()


def frames_dir(element_id: str, root: Path | str = ".") -> Path:
    """Return a target's frame directory: ``<root>/frames/<element_id>``."""
    return Path(root) / "frames" / element_id


def frame_path(element_id: str, slot: str, root: Path | str = ".") -> Path:
    """Return the PNG path for a target's slot: ``frames/<element_id>/<slot>.png``."""
    return frames_dir(element_id, root) / f"{slot}.png"


def exists(element_id: str, slot: str, root: Path | str = ".") -> bool:
    """Return True if the slot is already filled (so it must not be re-rendered)."""
    return frame_path(element_id, slot, root).exists()


def write_frame(
    element_id: str, slot: str, data: bytes, root: Path | str = "."
) -> bool:
    """Write PNG ``data`` to a slot, first-writer-wins.

    Creates the target's frame directory if needed. If the slot file already
    exists, leaves it untouched and returns False — frames are an immutable record.

    Returns:
        True if the file was written, False if a frame already occupied the slot.
    """
    path = frame_path(element_id, slot, root)
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


def list_frames(element_id: str, root: Path | str = ".") -> list[Path]:
    """Return a target's frame PNGs sorted chronologically (slot keys sort by time).

    Returns an empty list if the target has no frame directory yet.
    """
    directory = frames_dir(element_id, root)
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.png"))
