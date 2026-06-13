"""Load and validate ``config.toml`` into typed, frozen configuration objects.

This module is the boundary between a team-edited TOML file and the rest of the
pipeline. It parses the file, applies defaults, and validates every field with
error messages aimed at a non-programmer ("fix X in config.toml"), failing fast
before any network call or file write happens.

A target is supplied as a single pasted Onshape URL; this module parses the
document, workspace, and element IDs out of it. The element's type (assembly vs.
part studio) and human-friendly name are NOT in the URL — they are fetched from
the API at runtime and cached in the per-element state file, so the team only ever
has to paste a link.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .slots import resolve_timezone

# A standard Onshape document URL embeds three IDs and a workspace/version/microversion
# selector, e.g.
#   https://cad.onshape.com/documents/{did}/w/{wid}/e/{eid}
# We require the workspace ("w") form because the forward job tracks the live
# workspace; version ("v") and microversion ("m") links point at frozen snapshots.
_ONSHAPE_URL_RE = re.compile(
    r"/documents/(?P<did>[0-9A-Za-z]+)"
    r"/(?P<wvm>[wvm])/(?P<wvmid>[0-9A-Za-z]+)"
    r"/e/(?P<eid>[0-9A-Za-z]+)"
)


class ConfigError(Exception):
    """Raised when ``config.toml`` is missing, malformed, or fails validation.

    The message is written for the team member editing the file, not for a
    developer: it names the offending field and what a valid value looks like.
    """


@dataclass(frozen=True)
class Target:
    """One Onshape part studio or assembly to track, parsed from a pasted URL.

    The element id is the stable, collision-free key for this target: frames live
    in ``frames/<element_id>/`` and change-detection state in
    ``state/<element_id>.json``. The element's type and display name are fetched
    from the API at runtime, not stored here.

    Attributes:
        url: The original Onshape URL the team pasted (kept for error messages).
        document_id: The ``documents/<id>`` segment of the URL.
        workspace_id: The ``w/<id>`` segment of the URL.
        element_id: The ``e/<id>`` segment (the specific tab) — the folder/state key.
    """

    url: str
    document_id: str
    workspace_id: str
    element_id: str


@dataclass(frozen=True)
class Settings:
    """Global rendering and scheduling preferences shared by all targets.

    Attributes:
        image_width: Rendered PNG width in pixels.
        image_height: Rendered PNG height in pixels.
        view: A named Onshape view (e.g. ``"isometric"``) or a 12-number matrix string.
        timelapse_fps: Frames per second for the stitched timelapse video.
        keepalive: Whether the workflow commits a monthly no-op to defeat the
            60-day scheduled-workflow auto-disable.
        timezone: IANA timezone name the capture hours are interpreted in.
        capture_hours: Local hours (0-23) to take a screenshot at. One API call per
            hour listed. Empty means take no screenshots (capture paused).
    """

    image_width: int
    image_height: int
    view: str
    timelapse_fps: int
    keepalive: bool
    timezone: str
    capture_hours: tuple[int, ...]


@dataclass(frozen=True)
class Config:
    """The fully parsed and validated configuration."""

    settings: Settings
    targets: tuple[Target, ...]


# Defaults applied when a key is absent from ``[settings]``. Mirrors the shipped
# config.toml so an upgrade that adds a setting doesn't break an older file.
_SETTINGS_DEFAULTS: dict[str, object] = {
    "image_width": 1024,
    "image_height": 1024,
    "view": "isometric",
    "timelapse_fps": 10,
    "keepalive": True,
    "timezone": "UTC",
    "capture_hours": (8, 12, 16, 20),
}


def parse_onshape_url(url: str) -> tuple[str, str, str]:
    """Parse an Onshape document URL into ``(document_id, workspace_id, element_id)``.

    Args:
        url: A full Onshape URL of the form
            ``https://cad.onshape.com/documents/<did>/w/<wid>/e/<eid>`` (query
            string and trailing path allowed).

    Raises:
        ConfigError: if the URL doesn't contain the three IDs, or points at a
            version/microversion (``/v/`` or ``/m/``) instead of a live workspace
            (``/w/``). The message tells the team how to copy the right link.
    """
    match = _ONSHAPE_URL_RE.search(url)
    if not match:
        raise ConfigError(
            f"'{url}' is not a recognizable Onshape link. Open your part studio or "
            "assembly in Onshape and copy the URL from the browser address bar — it "
            "should look like "
            "https://cad.onshape.com/documents/<id>/w/<id>/e/<id>"
        )
    if match.group("wvm") != "w":
        raise ConfigError(
            f"'{url}' points at a fixed version/microversion (/{match.group('wvm')}/), "
            "not your live workspace. Copy the link while viewing the document in your "
            "workspace, so the URL contains '/w/'."
        )
    return match.group("did"), match.group("wvmid"), match.group("eid")


def _require_str(raw: dict, key: str, where: str) -> str:
    """Fetch ``key`` from ``raw`` as a non-empty string or raise ConfigError."""
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(
            f"{where}: '{key}' is required and must be a non-empty string."
        )
    return value.strip()


def _coerce_setting(key: str, value: object) -> object:
    """Validate and coerce a single ``[settings]`` value against its default's type."""
    if key == "capture_hours":
        if not isinstance(value, list):
            raise ConfigError(
                "[settings]: 'capture_hours' must be a list of hours, e.g. "
                "[8, 12, 16, 20]."
            )
        hours: list[int] = []
        for h in value:
            if isinstance(h, bool) or not isinstance(h, int) or not 0 <= h <= 23:
                raise ConfigError(
                    "[settings]: every entry in 'capture_hours' must be a whole "
                    f"number between 0 and 23 (got {h!r})."
                )
            hours.append(h)
        return tuple(sorted(set(hours)))
    if key == "timezone":
        if not isinstance(value, str) or not value.strip():
            raise ConfigError("[settings]: 'timezone' must be a non-empty string.")
        tz = value.strip()
        try:
            resolve_timezone(tz)
        except ValueError as exc:
            raise ConfigError(
                f"[settings]: 'timezone' {exc}. Use an IANA name like "
                '"America/New_York" or "UTC".'
            ) from exc
        return tz

    default = _SETTINGS_DEFAULTS[key]
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ConfigError(f"[settings]: '{key}' must be true or false.")
        return value
    if isinstance(default, int):
        # bool is a subclass of int; reject it explicitly so keepalive=1 isn't a size.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"[settings]: '{key}' must be a whole number.")
        if value <= 0:
            raise ConfigError(f"[settings]: '{key}' must be greater than zero.")
        return value
    # The only remaining default type is str (view).
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"[settings]: '{key}' must be a non-empty string.")
    return value.strip()


def _parse_settings(raw: dict) -> Settings:
    """Build a Settings object from the raw ``[settings]`` table, applying defaults."""
    if not isinstance(raw, dict):
        raise ConfigError("[settings] must be a table.")
    merged: dict[str, object] = {}
    for key, default in _SETTINGS_DEFAULTS.items():
        merged[key] = _coerce_setting(key, raw[key]) if key in raw else default
    unknown = set(raw) - set(_SETTINGS_DEFAULTS)
    if unknown:
        raise ConfigError(
            f"[settings]: unknown option(s) {sorted(unknown)}. "
            f"Valid options are {sorted(_SETTINGS_DEFAULTS)}."
        )
    return Settings(**merged)  # type: ignore[arg-type]


def _parse_target(raw: dict, index: int) -> Target:
    """Build one Target from a raw ``[[targets]]`` table containing a ``url``."""
    where = f"targets[{index}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: each target must be a table with a 'url'.")
    url = _require_str(raw, "url", where)
    unknown = set(raw) - {"url"}
    if unknown:
        raise ConfigError(
            f"{where}: unexpected key(s) {sorted(unknown)}. A target only needs a "
            "'url' — the element type and name are fetched from Onshape automatically."
        )
    document_id, workspace_id, element_id = parse_onshape_url(url)
    return Target(
        url=url,
        document_id=document_id,
        workspace_id=workspace_id,
        element_id=element_id,
    )


def parse_config(data: dict) -> Config:
    """Validate an already-parsed TOML mapping and return a Config.

    Separated from file reading so tests can exercise validation without touching
    the filesystem.

    Raises:
        ConfigError: on any missing/invalid field, a target URL that names the same
            element twice, or an empty target list. The message names the problem
            and how to fix it.
    """
    settings = _parse_settings(data.get("settings", {}))

    raw_targets = data.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ConfigError(
            "At least one [[targets]] entry is required. Add a [[targets]] block "
            "with a 'url' set to your Onshape document link."
        )

    targets = tuple(_parse_target(t, i) for i, t in enumerate(raw_targets))

    seen: set[str] = set()
    for t in targets:
        if t.element_id in seen:
            raise ConfigError(
                f"Two targets point at the same Onshape element ({t.element_id}). "
                "Each [[targets]] url must reference a different tab."
            )
        seen.add(t.element_id)

    return Config(settings=settings, targets=targets)


def load_config(path: str | Path = "config.toml") -> Config:
    """Read and validate ``config.toml`` from disk.

    Args:
        path: Path to the TOML file. Defaults to ``config.toml`` in the working dir.

    Raises:
        ConfigError: if the file is missing, not valid TOML, or fails validation.
    """
    p = Path(path)
    try:
        raw = p.read_bytes()
    except FileNotFoundError as exc:
        raise ConfigError(
            f"Configuration file '{p}' not found. Copy the example config.toml and "
            "fill in your document URL(s)."
        ) from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"'{p}' is not valid TOML: {exc}") from exc
    return parse_config(data)
