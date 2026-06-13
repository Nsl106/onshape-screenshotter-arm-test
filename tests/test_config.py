"""Tests for config parsing, URL parsing, and validation (no filesystem or network)."""

from __future__ import annotations

import pytest

from screenshotter.config import ConfigError, parse_config, parse_onshape_url

# A realistic Onshape workspace URL (24-hex IDs, as Onshape issues them).
_URL = (
    "https://cad.onshape.com/documents/aaaaaaaaaaaaaaaaaaaaaaaa"
    "/w/bbbbbbbbbbbbbbbbbbbbbbbb/e/cccccccccccccccccccccccc"
)


def _valid_data() -> dict:
    """A minimal valid parsed-TOML mapping; tests mutate copies of this."""
    return {
        "settings": {
            "image_width": 800,
            "image_height": 600,
            "view": "isometric",
            "timelapse_fps": 15,
            "keepalive": False,
        },
        "targets": [{"url": _URL}],
    }


# --- URL parsing ----------------------------------------------------------------


def test_parse_url_extracts_three_ids() -> None:
    did, wid, eid = parse_onshape_url(_URL)
    assert did == "a" * 24
    assert wid == "b" * 24
    assert eid == "c" * 24


def test_parse_url_tolerates_query_and_trailing_path() -> None:
    did, wid, eid = parse_onshape_url(_URL + "?configuration=default")
    assert (did, wid, eid) == ("a" * 24, "b" * 24, "c" * 24)


def test_parse_url_rejects_version_link() -> None:
    version_url = _URL.replace("/w/", "/v/")
    with pytest.raises(ConfigError, match="live workspace"):
        parse_onshape_url(version_url)


def test_parse_url_rejects_garbage() -> None:
    with pytest.raises(ConfigError, match="not a recognizable Onshape link"):
        parse_onshape_url("https://example.com/whatever")


# --- config validation ----------------------------------------------------------


def test_valid_config_parses() -> None:
    cfg = parse_config(_valid_data())
    assert cfg.settings.image_width == 800
    assert cfg.settings.keepalive is False
    assert len(cfg.targets) == 1
    t = cfg.targets[0]
    assert t.url == _URL
    assert t.document_id == "a" * 24
    assert t.workspace_id == "b" * 24
    assert t.element_id == "c" * 24


def test_settings_defaults_applied_when_absent() -> None:
    data = _valid_data()
    del data["settings"]
    cfg = parse_config(data)
    # Defaults mirror the shipped config.toml.
    assert cfg.settings.image_width == 1024
    assert cfg.settings.view == "isometric"
    assert cfg.settings.timelapse_fps == 10
    assert cfg.settings.keepalive is True
    assert cfg.settings.timezone == "UTC"
    assert cfg.settings.capture_hours == (8, 12, 16, 20)


def test_missing_url_raises() -> None:
    data = _valid_data()
    del data["targets"][0]["url"]
    with pytest.raises(ConfigError, match="url"):
        parse_config(data)


def test_bad_url_raises() -> None:
    data = _valid_data()
    data["targets"][0]["url"] = "not-a-url"
    with pytest.raises(ConfigError, match="Onshape link"):
        parse_config(data)


def test_unexpected_target_key_raises() -> None:
    data = _valid_data()
    data["targets"][0]["element_type"] = "assembly"
    with pytest.raises(ConfigError, match="element type and name are fetched"):
        parse_config(data)


def test_duplicate_element_raises() -> None:
    data = _valid_data()
    data["targets"].append({"url": _URL})
    with pytest.raises(ConfigError, match="same Onshape element"):
        parse_config(data)


def test_distinct_elements_ok() -> None:
    data = _valid_data()
    other = _URL.replace("/e/" + "c" * 24, "/e/" + "d" * 24)
    data["targets"].append({"url": other})
    cfg = parse_config(data)
    assert {t.element_id for t in cfg.targets} == {"c" * 24, "d" * 24}


def test_no_targets_raises() -> None:
    data = _valid_data()
    data["targets"] = []
    with pytest.raises(ConfigError, match="targets"):
        parse_config(data)


def test_missing_targets_key_raises() -> None:
    data = _valid_data()
    del data["targets"]
    with pytest.raises(ConfigError, match="targets"):
        parse_config(data)


def test_negative_image_size_raises() -> None:
    data = _valid_data()
    data["settings"]["image_width"] = -10
    with pytest.raises(ConfigError, match="image_width"):
        parse_config(data)


def test_bool_for_int_setting_raises() -> None:
    data = _valid_data()
    data["settings"]["image_width"] = True
    with pytest.raises(ConfigError, match="image_width"):
        parse_config(data)


def test_unknown_setting_raises() -> None:
    data = _valid_data()
    data["settings"]["bogus"] = 1
    with pytest.raises(ConfigError, match="unknown"):
        parse_config(data)


def test_capture_hours_and_timezone_parse() -> None:
    data = _valid_data()
    data["settings"].update(timezone="America/New_York", capture_hours=[20, 8, 8, 14])
    cfg = parse_config(data)
    assert cfg.settings.timezone == "America/New_York"
    # Deduplicated and sorted.
    assert cfg.settings.capture_hours == (8, 14, 20)


def test_empty_capture_hours_allowed() -> None:
    data = _valid_data()
    data["settings"]["capture_hours"] = []
    assert parse_config(data).settings.capture_hours == ()


def test_capture_hour_out_of_range_raises() -> None:
    data = _valid_data()
    data["settings"]["capture_hours"] = [8, 24]
    with pytest.raises(ConfigError, match="between 0 and 23"):
        parse_config(data)


def test_capture_hours_not_a_list_raises() -> None:
    data = _valid_data()
    data["settings"]["capture_hours"] = 8
    with pytest.raises(ConfigError, match="must be a list"):
        parse_config(data)


def test_bad_timezone_raises() -> None:
    data = _valid_data()
    data["settings"]["timezone"] = "Mars/Olympus_Mons"
    with pytest.raises(ConfigError, match="timezone"):
        parse_config(data)
