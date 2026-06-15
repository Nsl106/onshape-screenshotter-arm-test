"""Capture scenario tests with a fake client (no network, no real files)."""

from __future__ import annotations

from datetime import UTC, datetime

from screenshotter import frames
from screenshotter.capture import (
    ALREADY_DONE,
    CAPTURED,
    ERROR,
    OFF_HOURS,
    SLOT_FILLED,
    UNCHANGED,
    run,
)
from screenshotter.config import Config, Settings, Target
from screenshotter.onshape import ElementMetadata, OnshapeAPIError
from screenshotter.state import State, read_state, state_path, write_state


def _utc(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def _target(eid="E1", did="D1", wid="W1") -> Target:
    return Target(
        url=f"https://cad.onshape.com/documents/{did}/w/{wid}/e/{eid}",
        document_id=did,
        workspace_id=wid,
        element_id=eid,
    )


_ALL_HOURS = tuple(range(24))  # capture at any hour, for tests that aren't gating


def _config(*targets: Target, capture_hours=_ALL_HOURS, tz="UTC") -> Config:
    settings = Settings(
        image_width=64,
        image_height=64,
        view="isometric",
        timelapse_fps=10,
        keepalive=True,
        timezone=tz,
        capture_hours=tuple(capture_hours),
    )
    return Config(settings=settings, targets=targets or (_target(),))


class FakeClient:
    """Stand-in for OnshapeClient. ``image`` is the bytes render returns."""

    def __init__(
        self,
        image: bytes = b"IMG-A",
        element_type: str = "assembly",
        name: str = "Drivetrain",
        doc_name: str = "Robot 2026",
        render_error: bool = False,
    ) -> None:
        self.image = image
        self._element_type = element_type
        self._name = name
        self._doc_name = doc_name
        self._render_error = render_error
        self.rendered = 0
        self.metadata_calls = 0
        self.doc_name_calls = 0

    def get_element_metadata(self, target):
        self.metadata_calls += 1
        return ElementMetadata(name=self._name, element_type=self._element_type)

    def get_document_name(self, did):
        self.doc_name_calls += 1
        return self._doc_name

    def render_shaded_view(self, target, element_type, *, view, width, height):
        self.rendered += 1
        if self._render_error:
            raise OnshapeAPIError(500, "render boom")
        return self.image


def test_first_run_captures_and_caches_metadata(tmp_path) -> None:
    client = FakeClient(image=b"PNGBYTES")
    [result] = run(_config(), client, now=_utc(2024, 1, 5, 9), root=tmp_path)
    assert result.status == CAPTURED
    assert result.detail == "2024-01-05_09"
    assert (
        frames.frame_path("E1", "2024-01-05_09", tmp_path).read_bytes() == b"PNGBYTES"
    )
    state = read_state(state_path("E1", tmp_path))
    assert state.last_image_hash is not None
    assert state.display_name == "Drivetrain"
    assert state.document_name == "Robot 2026"
    # First run cost: metadata + doc name + render.
    assert (client.metadata_calls, client.doc_name_calls, client.rendered) == (1, 1, 1)


def test_unchanged_image_skips_and_costs_one_call(tmp_path) -> None:
    # Pre-seed state with the fingerprint of the image the client will return.
    img = b"SAME"
    write_state(
        state_path("E1", tmp_path),
        State(
            last_image_hash=frames.image_fingerprint(img),
            element_type="assembly",
            display_name="Drivetrain",
            document_name="Robot 2026",
        ),
    )
    client = FakeClient(image=img)
    [result] = run(_config(), client, now=_utc(2024, 1, 5, 12), root=tmp_path)
    assert result.status == UNCHANGED
    assert not frames.exists("E1", "2024-01-05_12", tmp_path)
    # One render call, and no metadata re-fetch (already cached).
    assert (client.metadata_calls, client.doc_name_calls, client.rendered) == (0, 0, 1)


def test_changed_image_writes_new_frame(tmp_path) -> None:
    write_state(
        state_path("E1", tmp_path),
        State(
            last_image_hash=frames.image_fingerprint(b"OLD"),
            element_type="assembly",
            display_name="Drivetrain",
        ),
    )
    client = FakeClient(image=b"NEW")
    [result] = run(_config(), client, now=_utc(2024, 1, 5, 12), root=tmp_path)
    assert result.status == CAPTURED
    assert frames.frame_path("E1", "2024-01-05_12", tmp_path).read_bytes() == b"NEW"
    assert read_state(state_path("E1", tmp_path)).last_image_hash == (
        frames.image_fingerprint(b"NEW")
    )


def test_changed_but_slot_filled_skips(tmp_path) -> None:
    frames.write_frame("E1", "2024-01-05_09", b"existing", tmp_path)
    write_state(
        state_path("E1", tmp_path),
        State(
            last_image_hash=frames.image_fingerprint(b"OLD"), element_type="assembly"
        ),
    )
    client = FakeClient(image=b"NEW")
    [result] = run(_config(), client, now=_utc(2024, 1, 5, 9, 30), root=tmp_path)
    assert result.status == SLOT_FILLED
    assert (
        frames.frame_path("E1", "2024-01-05_09", tmp_path).read_bytes() == b"existing"
    )


def test_off_capture_hour_skips_with_zero_calls(tmp_path) -> None:
    client = FakeClient()
    # Capture hours 8/12/16/20; run at 04:00 -> none due yet today, no calls at all.
    [result] = run(
        _config(capture_hours=(8, 12, 16, 20)),
        client,
        now=_utc(2024, 1, 5, 4),
        root=tmp_path,
    )
    assert result.status == OFF_HOURS
    assert client.rendered == 0
    assert client.metadata_calls == 0


def test_on_capture_hour_runs(tmp_path) -> None:
    client = FakeClient()
    [result] = run(
        _config(capture_hours=(8, 12, 16, 20)),
        client,
        now=_utc(2024, 1, 5, 12),
        root=tmp_path,
    )
    assert result.status == CAPTURED


def test_catch_up_fires_after_a_missed_hour(tmp_path) -> None:
    # GitHub never fired during the 08:00 hour; the next run at 10:30 still captures.
    client = FakeClient()
    [result] = run(
        _config(capture_hours=(8, 12, 16)),
        client,
        now=_utc(2024, 1, 5, 10, 30),
        root=tmp_path,
    )
    assert result.status == CAPTURED
    assert client.rendered == 1
    # The serviced capture hour is recorded as the 08 slot it caught up.
    assert read_state(state_path("E1", tmp_path)).last_capture_target == "2024-01-05:08"


def test_already_serviced_hour_skips_without_call(tmp_path) -> None:
    # State already shows the 12 slot done today -> later runs that period skip free.
    write_state(
        state_path("E1", tmp_path),
        State(last_capture_target="2024-01-05:12", element_type="assembly"),
    )
    client = FakeClient()
    [result] = run(
        _config(capture_hours=(8, 12, 16)),
        client,
        now=_utc(2024, 1, 5, 13),
        root=tmp_path,
    )
    assert result.status == ALREADY_DONE
    assert client.rendered == 0
    assert client.metadata_calls == 0


def test_next_capture_hour_renders_again(tmp_path) -> None:
    # Having done the 08 slot, a run at 12:xx services the new 12 slot.
    write_state(
        state_path("E1", tmp_path),
        State(last_capture_target="2024-01-05:08", element_type="assembly"),
    )
    client = FakeClient(image=b"NEW")
    [result] = run(
        _config(capture_hours=(8, 12, 16)),
        client,
        now=_utc(2024, 1, 5, 12, 5),
        root=tmp_path,
    )
    assert result.status == CAPTURED
    assert read_state(state_path("E1", tmp_path)).last_capture_target == "2024-01-05:12"


def test_dry_run_writes_nothing(tmp_path) -> None:
    client = FakeClient(image=b"IMG")
    [result] = run(
        _config(), client, now=_utc(2024, 1, 5, 9), root=tmp_path, dry_run=True
    )
    assert result.status == CAPTURED
    assert "dry-run" in result.detail
    assert not frames.exists("E1", "2024-01-05_09", tmp_path)
    assert read_state(state_path("E1", tmp_path)).last_image_hash is None


def test_one_target_errors_other_succeeds(tmp_path) -> None:
    t_ok = _target(eid="OK", did="DOK")
    t_bad = _target(eid="BAD", did="DBAD")

    class HalfBroken(FakeClient):
        def render_shaded_view(self, target, element_type, **kw):
            if target.document_id == "DBAD":
                self.rendered += 1
                raise OnshapeAPIError(500, "render boom")
            return super().render_shaded_view(target, element_type, **kw)

    client = HalfBroken(image=b"IMG")
    results = run(_config(t_ok, t_bad), client, now=_utc(2024, 1, 5, 9), root=tmp_path)
    by_id = {r.element_id: r for r in results}
    assert by_id["OK"].status == CAPTURED
    assert by_id["BAD"].status == ERROR
    assert frames.exists("OK", "2024-01-05_09", tmp_path)


def test_readme_index_updated_after_capture(tmp_path) -> None:
    (tmp_path / "README.md").write_text(
        "# Title\n<!-- targets:start -->\nold\n<!-- targets:end -->\n", encoding="utf-8"
    )
    client = FakeClient(image=b"IMG")
    run(_config(), client, now=_utc(2024, 1, 5, 9), root=tmp_path)
    text = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "Robot 2026 / Drivetrain" in text
    assert "frames/E1/" in text
    assert "1 frame" in text
