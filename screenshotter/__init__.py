"""Onshape Screenshotter — render an Onshape document on a schedule into frames.

The package is a small pipeline. Each module owns one stage:

- ``config``    — load and validate ``config.toml`` into typed, frozen objects.
- ``onshape``   — the only module that talks to the network: auth, history paging,
                  shaded-view rendering, rate-limit handling.
- ``slots``     — pure logic: slot keys and the capture-hours gate.
- ``state``     — read/write the per-target ``state/<element_id>.json`` change marker.
- ``frames``    — frame paths, existence checks, image writing (first-writer-wins).
- ``index``     — maintain the auto-generated "Tracked CAD" section of the README.
- ``capture``   — the scheduled forward job entrypoint.
- ``timelapse`` — stitch committed frames into a video via ffmpeg.
"""

__version__ = "0.1.0"
