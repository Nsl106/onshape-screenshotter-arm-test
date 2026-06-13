"""Tests for the Onshape client using a fake requests.Session (no real network)."""

from __future__ import annotations

import base64

import pytest

from screenshotter.config import Target
from screenshotter.onshape import (
    DEFAULT_BASE_URL,
    OnshapeAPIError,
    OnshapeAuthError,
    OnshapeClient,
    OnshapeQuotaError,
    resolve_view,
)

_TARGET = Target(
    url="https://cad.onshape.com/documents/D/w/W/e/E",
    document_id="D",
    workspace_id="W",
    element_id="E",
)


class FakeResponse:
    def __init__(self, status_code: int, json_data=None, headers=None) -> None:
        self.status_code = status_code
        self._json = json_data
        self.headers = headers or {}

    def json(self):
        return self._json


class FakeSession:
    """Records each request and returns queued responses in order."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def request(
        self, method, url, *, params=None, auth=None, headers=None, timeout=None
    ):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "params": params,
                "auth": auth,
                "headers": headers,
            }
        )
        return self._responses.pop(0)


def _client(
    responses: list[FakeResponse], **kwargs
) -> tuple[OnshapeClient, FakeSession]:
    session = FakeSession(responses)
    slept: list[float] = []
    client = OnshapeClient(
        "ACCESS",
        "SECRET",
        session=session,
        sleep=slept.append,
        **kwargs,
    )
    client._slept = slept  # type: ignore[attr-defined]
    return client, session


# --- construction / auth --------------------------------------------------------


def test_missing_credentials_raises() -> None:
    with pytest.raises(OnshapeAuthError):
        OnshapeClient("", "")


def test_from_env_reads_keys(monkeypatch) -> None:
    monkeypatch.setenv("ONSHAPE_ACCESS_KEY", "a")
    monkeypatch.setenv("ONSHAPE_SECRET_KEY", "b")
    monkeypatch.delenv("ONSHAPE_BASE_URL", raising=False)
    client = OnshapeClient.from_env(session=FakeSession([]))
    assert client._auth == ("a", "b")
    assert client._base_url == DEFAULT_BASE_URL


def test_from_env_missing_raises(monkeypatch) -> None:
    monkeypatch.delenv("ONSHAPE_ACCESS_KEY", raising=False)
    monkeypatch.delenv("ONSHAPE_SECRET_KEY", raising=False)
    with pytest.raises(OnshapeAuthError):
        OnshapeClient.from_env()


def test_request_sends_basic_auth_and_json_accept() -> None:
    client, session = _client([FakeResponse(200, {"name": "Doc"})])
    client.get_document_name("D")
    call = session.calls[0]
    assert call["auth"] == ("ACCESS", "SECRET")
    assert call["headers"]["Accept"] == "application/json"
    assert call["url"] == f"{DEFAULT_BASE_URL}/documents/D"


# --- view resolution ------------------------------------------------------------


def test_resolve_view_translates_isometric() -> None:
    assert resolve_view("isometric").startswith("0.707")
    assert resolve_view("ISO").startswith("0.707")


def test_resolve_view_passes_through_other_names_and_matrices() -> None:
    assert resolve_view("front") == "front"
    assert resolve_view("1,0,0,0,0,1,0,0,0,0,1,0") == "1,0,0,0,0,1,0,0,0,0,1,0"


# --- element metadata -----------------------------------------------------------


def test_get_element_metadata_maps_type_and_name() -> None:
    body = [{"id": "E", "name": "Drivetrain", "elementType": "ASSEMBLY"}]
    client, _ = _client([FakeResponse(200, body)])
    meta = client.get_element_metadata(_TARGET)
    assert meta.name == "Drivetrain"
    assert meta.element_type == "assembly"


def test_get_element_metadata_unrenderable_type_raises() -> None:
    body = [{"id": "E", "name": "Sheet 1", "elementType": "DRAWING"}]
    client, _ = _client([FakeResponse(200, body)])
    with pytest.raises(OnshapeAPIError, match="only part studios and assemblies"):
        client.get_element_metadata(_TARGET)


def test_get_element_metadata_missing_element_raises() -> None:
    client, _ = _client([FakeResponse(200, [{"id": "OTHER"}])])
    with pytest.raises(OnshapeAPIError, match="not found"):
        client.get_element_metadata(_TARGET)


# --- retry / error handling -----------------------------------------------------


def test_429_is_retried_then_succeeds() -> None:
    responses = [
        FakeResponse(429, headers={"Retry-After": "0"}),
        FakeResponse(200, {"name": "Doc"}),
    ]
    client, session = _client(responses)
    assert client.get_document_name("D") == "Doc"
    assert len(session.calls) == 2
    assert client._slept == [0.0]  # honored Retry-After: 0


def test_429_gives_up_after_max_attempts() -> None:
    responses = [FakeResponse(429, headers={"Retry-After": "0"}) for _ in range(3)]
    client, _ = _client(responses, max_attempts=3)
    with pytest.raises(OnshapeAPIError) as exc:
        client.get_document_name("D")
    assert exc.value.status == 429


def test_402_raises_quota_error_without_retry() -> None:
    client, session = _client([FakeResponse(402)])
    with pytest.raises(OnshapeQuotaError, match="annual API-call quota"):
        client.get_document_name("D")
    assert len(session.calls) == 1  # not retried — quota won't clear by waiting


def test_auth_failure_raises_immediately() -> None:
    client, session = _client([FakeResponse(403)])
    with pytest.raises(OnshapeAuthError):
        client.get_document_name("D")
    assert len(session.calls) == 1  # not retried


def test_other_4xx_is_not_retried() -> None:
    client, session = _client([FakeResponse(400)])
    with pytest.raises(OnshapeAPIError) as exc:
        client.get_document_name("D")
    assert exc.value.status == 400
    assert len(session.calls) == 1


def test_credentials_never_in_error_messages() -> None:
    client, _ = _client([FakeResponse(500) for _ in range(5)])
    with pytest.raises(OnshapeAPIError) as exc:
        client.get_document_name("D")
    assert "SECRET" not in str(exc.value)
    assert "ACCESS" not in str(exc.value)


# --- shaded view rendering ------------------------------------------------------


def test_render_shaded_view_decodes_base64_png() -> None:
    png = b"\x89PNG\r\n\x1a\nfake-bytes"
    b64 = base64.b64encode(png).decode()
    client, session = _client([FakeResponse(200, {"images": [b64]})])
    out = client.render_shaded_view(
        _TARGET, "assembly", view="isometric", width=800, height=600
    )
    assert out == png
    call = session.calls[0]
    # Renders the current workspace state (w/), not a past microversion.
    assert "/assemblies/d/D/w/W/e/E/shadedviews" in call["url"]
    assert call["params"]["outputWidth"] == 800
    assert call["params"]["outputHeight"] == 600
    assert call["params"]["pixelSize"] == 0.0
    assert call["params"]["viewMatrix"].startswith("0.707")


def test_render_shaded_view_partstudio_uses_partstudios_collection() -> None:
    b64 = base64.b64encode(b"x").decode()
    client, session = _client([FakeResponse(200, {"images": [b64]})])
    client.render_shaded_view(_TARGET, "partstudio", view="front", width=10, height=10)
    assert "/partstudios/d/D/w/W/e/E/shadedviews" in session.calls[0]["url"]


def test_render_shaded_view_empty_images_raises() -> None:
    client, _ = _client([FakeResponse(200, {"images": []})])
    with pytest.raises(OnshapeAPIError, match="no image"):
        client.render_shaded_view(_TARGET, "assembly", view="iso", width=10, height=10)
