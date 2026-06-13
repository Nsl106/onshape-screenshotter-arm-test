"""Onshape API client — the only module in the pipeline that touches the network.

Wraps the subset of the Onshape REST API this tool needs:

- list a document's elements (to learn a target's type and display name),
- render a shaded-view PNG of the current workspace state.

That's one render call per capture run; change detection happens locally by
fingerprinting the image, so no history lookup is needed.

All requests use HTTP Basic auth with an API key pair read from environment
variables. Credentials are never placed in log lines or exception messages.

API details verified against the Onshape developer docs and the published OpenAPI
spec (onshape-public/onshape-clients):
- Base path ``/api/v10`` on ``https://cad.onshape.com``.
- ``GET /{partstudios|assemblies}/d/{did}/{wvm}/{wvmid}/e/{eid}/shadedviews`` —
  params ``viewMatrix`` (named view or 12-number matrix), ``outputWidth``,
  ``outputHeight``, ``pixelSize`` (0 fits the model to the frame), ``edges``;
  returns a JSON object with an ``images`` array of base64-encoded PNGs.
- ``GET /documents/d/{did}/{wvm}/{wvmid}/elements`` — each element has ``id``,
  ``name``, ``elementType`` (``PARTSTUDIO`` / ``ASSEMBLY`` / ...).

Onshape does not publish exact rate-limit numbers; limits vary per endpoint and a
breach returns HTTP 429. The single request helper retries on 429 (honoring
``Retry-After`` when present) and on transient 5xx, with capped exponential backoff
plus jitter.
"""

from __future__ import annotations

import base64
import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass

import requests

from .config import Target

DEFAULT_BASE_URL = "https://cad.onshape.com/api/v10"

# Map Onshape's elementType enum to our config vocabulary and the URL collection.
_ELEMENT_TYPE_MAP = {"PARTSTUDIO": "partstudio", "ASSEMBLY": "assembly"}
_COLLECTION = {"partstudio": "partstudios", "assembly": "assemblies"}

# Named views: Onshape's viewMatrix parameter accepts the orthographic names
# natively (its default is "front"), but not "isometric" — so we translate that
# one ourselves to the standard view-cube isometric matrix and pass anything else
# (a native name like "front"/"top", or a raw 12-number matrix) through unchanged.
_ISOMETRIC_VIEW_MATRIX = (
    "0.707106781,0.707106781,0,0,"
    "-0.408248290,0.408248290,0.816496581,0,"
    "0.577350269,-0.577350269,0.577350269,0"
)
_NAMED_VIEW_MATRICES = {
    "isometric": _ISOMETRIC_VIEW_MATRIX,
    "iso": _ISOMETRIC_VIEW_MATRIX,
}


class OnshapeError(Exception):
    """Base class for all errors raised by the Onshape client."""


class OnshapeAuthError(OnshapeError):
    """Missing API credentials, or the server rejected them (HTTP 401/403)."""


class OnshapeAPIError(OnshapeError):
    """A non-success HTTP response that isn't an auth failure.

    Carries the status code and a short, credential-free context string.
    """

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


class OnshapeQuotaError(OnshapeError):
    """The account's annual API-call quota is exhausted (HTTP 402).

    Not retryable and not a credential problem — the account is simply out of API
    calls for the year. Raised so callers can surface a distinct, actionable hint.
    """


@dataclass(frozen=True)
class ElementMetadata:
    """The fetched, not-in-the-URL facts about a target element.

    Attributes:
        name: The element (tab) name, used as the human-friendly display label.
        element_type: ``"partstudio"`` or ``"assembly"`` — selects the render endpoint.
    """

    name: str
    element_type: str


def resolve_view(view: str) -> str:
    """Translate a config ``view`` value into a viewMatrix query value.

    ``"isometric"``/``"iso"`` become the standard isometric matrix; every other
    value (a native Onshape view name or a raw 12-number matrix) is passed through.
    """
    return _NAMED_VIEW_MATRICES.get(view.strip().lower(), view)


class OnshapeClient:
    """A thin, rate-limit-aware client for the Onshape REST API.

    Args:
        access_key: Onshape API access key (the Basic-auth username).
        secret_key: Onshape API secret key (the Basic-auth password).
        base_url: API base, including version path. Defaults to the v10 endpoint.
        session: Optional pre-built ``requests.Session`` (injected by tests).
        max_attempts: Total attempts per request before giving up (>= 1).
        backoff_base: Base seconds for exponential backoff between retries.
        sleep: Sleep function, injectable so tests don't actually wait.
    """

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        session: requests.Session | None = None,
        max_attempts: int = 5,
        backoff_base: float = 0.5,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        if not access_key or not secret_key:
            raise OnshapeAuthError(
                "Onshape API credentials are missing. Set ONSHAPE_ACCESS_KEY and "
                "ONSHAPE_SECRET_KEY."
            )
        self._auth = (access_key, secret_key)
        self._base_url = base_url.rstrip("/")
        self._session = session or requests.Session()
        self._max_attempts = max(1, max_attempts)
        self._backoff_base = backoff_base
        self._sleep = sleep or time.sleep

    @classmethod
    def from_env(cls, **kwargs: object) -> OnshapeClient:
        """Build a client from ``ONSHAPE_ACCESS_KEY`` / ``ONSHAPE_SECRET_KEY``.

        ``ONSHAPE_BASE_URL`` overrides the API base if set. Raises OnshapeAuthError
        with a fix-it message when either key is absent.
        """
        access = os.environ.get("ONSHAPE_ACCESS_KEY", "")
        secret = os.environ.get("ONSHAPE_SECRET_KEY", "")
        base = os.environ.get("ONSHAPE_BASE_URL", DEFAULT_BASE_URL)
        return cls(access, secret, base_url=base, **kwargs)  # type: ignore[arg-type]

    # --- low-level request helper ------------------------------------------------

    def _backoff_seconds(self, attempt: int) -> float:
        """Exponential backoff with jitter, so concurrent runs don't sync up."""
        return self._backoff_base * (2**attempt) * (0.5 + random.random() / 2)

    def _retry_after_seconds(self, response: requests.Response, attempt: int) -> float:
        """Seconds before the next attempt: Retry-After if given, else backoff."""
        header = response.headers.get("Retry-After")
        if header:
            try:
                return max(0.0, float(header))
            except ValueError:
                pass  # Non-numeric (HTTP-date) Retry-After — fall back to backoff.
        return self._backoff_seconds(attempt)

    def _request(
        self, method: str, path: str, *, params: dict[str, object] | None = None
    ) -> requests.Response:
        """Perform one API request with retries; return the successful response.

        Retries on 429 (rate limited) and transient 5xx; raises OnshapeAuthError on
        401/403 and OnshapeAPIError on any other non-2xx. Credentials never appear
        in raised messages — only the method, path, and status do.

        Raises:
            OnshapeAuthError: on missing/invalid credentials.
            OnshapeAPIError: on other non-success responses or network failure.
        """
        url = f"{self._base_url}{path}"
        last_error: str = ""
        for attempt in range(self._max_attempts):
            try:
                response = self._session.request(
                    method,
                    url,
                    params=params,
                    auth=self._auth,
                    headers={"Accept": "application/json"},
                    timeout=60,
                )
            except requests.RequestException as exc:
                # Network-level failure: retry, but keep the exception type out of
                # the message in case a library ever embeds the URL with auth.
                last_error = f"network error contacting Onshape ({type(exc).__name__})"
                if attempt < self._max_attempts - 1:
                    self._sleep(self._backoff_seconds(attempt))
                    continue
                raise OnshapeAPIError(0, f"Onshape unreachable: {last_error}.") from exc

            if response.status_code in (401, 403):
                raise OnshapeAuthError(
                    "Onshape rejected the API credentials "
                    f"(HTTP {response.status_code}). Check ONSHAPE_ACCESS_KEY / "
                    "ONSHAPE_SECRET_KEY and that the key owner can access this "
                    "document."
                )
            if 200 <= response.status_code < 300:
                return response
            if response.status_code == 402:
                # Onshape returns 402 when the account's *annual* API-call quota is
                # exhausted (distinct from the per-minute 429 rate limit). It won't
                # clear by retrying, so fail fast with an actionable message.
                raise OnshapeQuotaError(
                    "Onshape's annual API-call quota for this account is used up "
                    "(HTTP 402). It resets each year; more calls can be requested "
                    "from Onshape (api-support@onshape.com)."
                )
            if response.status_code == 429 or 500 <= response.status_code < 600:
                last_error = f"HTTP {response.status_code} from {method} {path}"
                if attempt < self._max_attempts - 1:
                    self._sleep(self._retry_after_seconds(response, attempt))
                    continue
            else:
                raise OnshapeAPIError(
                    response.status_code,
                    f"Onshape request failed: HTTP {response.status_code} "
                    f"from {method} {path}.",
                )
        raise OnshapeAPIError(
            429,
            f"Onshape request gave up after {self._max_attempts} attempts "
            f"({last_error}).",
        )

    # --- public API --------------------------------------------------------------

    def get_element_metadata(self, target: Target) -> ElementMetadata:
        """Fetch a target element's display name and type from its document.

        Lists the document's elements and matches the target's element id. The
        element type isn't in the pasted URL, so this is how the pipeline learns
        whether to call the part-studio or assembly render endpoint.

        Raises:
            OnshapeAPIError: if the element id isn't found in the document, or the
                element is a type this tool can't render (e.g. a drawing).
        """
        path = f"/documents/d/{target.document_id}/w/{target.workspace_id}/elements"
        response = self._request("GET", path, params={"elementId": target.element_id})
        elements = response.json()
        for element in elements:
            if element.get("id") == target.element_id:
                raw_type = (element.get("elementType") or "").upper()
                mapped = _ELEMENT_TYPE_MAP.get(raw_type)
                if mapped is None:
                    raise OnshapeAPIError(
                        422,
                        f"Element {target.element_id} is a {raw_type or 'unknown'} "
                        "tab; only part studios and assemblies can be rendered.",
                    )
                name = element.get("name") or target.element_id
                return ElementMetadata(name=name, element_type=mapped)
        raise OnshapeAPIError(
            404,
            f"Element {target.element_id} was not found in document "
            f"{target.document_id}. Check the URL in config.toml.",
        )

    def get_document_name(self, document_id: str) -> str:
        """Fetch a document's name (used as extra context in the README index)."""
        response = self._request("GET", f"/documents/{document_id}")
        return response.json().get("name") or document_id

    def render_shaded_view(
        self,
        target: Target,
        element_type: str,
        *,
        view: str,
        width: int,
        height: int,
        pixel_size: float = 0.0,
    ) -> bytes:
        """Render a shaded-view PNG of a target's current workspace state.

        Renders on the ``w/{workspace}`` path — the live state right now — in a
        single API call. Change detection is done afterward by fingerprinting the
        returned image, so no separate history lookup is needed. ``pixel_size`` of 0
        lets Onshape fit the whole model to the frame, keeping framing consistent as
        the model grows.

        Args:
            element_type: ``"partstudio"`` or ``"assembly"`` (from element metadata).

        Returns:
            The raw PNG bytes of the first image in the response.

        Raises:
            OnshapeAPIError: on request failure or an empty/invalid image response.
        """
        collection = _COLLECTION[element_type]
        path = (
            f"/{collection}/d/{target.document_id}/w/{target.workspace_id}"
            f"/e/{target.element_id}/shadedviews"
        )
        params = {
            "viewMatrix": resolve_view(view),
            "outputWidth": width,
            "outputHeight": height,
            "pixelSize": pixel_size,
            "edges": "show",
        }
        response = self._request("GET", path, params=params)
        payload = response.json()
        images = payload.get("images") if isinstance(payload, dict) else None
        if not images:
            raise OnshapeAPIError(
                502,
                f"Onshape returned no image for element {target.element_id}.",
            )
        try:
            return base64.b64decode(images[0])
        except (ValueError, TypeError) as exc:
            raise OnshapeAPIError(
                502, "Onshape returned an image that could not be decoded."
            ) from exc
