"""Minimal WebDriverAgent (WDA) HTTP client — the direct iOS backend.

:class:`WdaClient` is the SDK-level target for driving an iOS device whose
WebDriverAgent is already running (USB real device: ``iproxy 8100 8100``)::

    bot.ai(WdaClient("http://127.0.0.1:8100"), "...")

qirabot needs ~10 of WDA's endpoints — screenshot, tap/swipe, keys, buttons,
window size — all stable for years (they are what Appium's XCUITest driver
itself depends on). The wire shapes below are replicated verbatim from
facebook-wda (MIT, github.com/openatx/facebook-wda), so this client sends
exactly the requests that library is known to work with, over qirabot's
existing core dependency httpx instead of facebook-wda's legacy stack.

Sessions: WDA scopes interaction endpoints under ``/session/<id>``. The client
reuses WDA's current session when one exists (``GET /status`` reports it) or
creates a default one, and transparently re-creates it once when WDA reports
``invalid session id`` (e.g. after the foreground app crashed).
"""

from __future__ import annotations

import base64
from typing import Any

import httpx

from qirabot.exceptions import QirabotError

_DEFAULT_URL = "http://127.0.0.1:8100"


class WdaError(QirabotError):
    """A WDA request failed (device-side error or unreachable agent)."""


class WdaClient:
    """Client for one WebDriverAgent instance.

    Args:
        url: WDA server URL. USB real device: keep the default and run
            ``iproxy 8100 8100``; network device: ``http://<ip>:8100``.
        timeout: per-request timeout in seconds (screenshots on slow devices
            are the long pole).
    """

    def __init__(self, url: str = _DEFAULT_URL, timeout: float = 30.0) -> None:
        url = url if url.startswith("http") else f"http://{url}"
        self.url = url.rstrip("/")
        self._http = httpx.Client(base_url=self.url, timeout=timeout)
        self._session_id: str | None = None
        # WDA >= 6.0 renamed /wda/tap/0 to /wda/tap; probe once, remember.
        self._legacy_tap: bool | None = None

    def __repr__(self) -> str:
        return f"WdaClient(url={self.url!r})"

    # ---- plumbing -----------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        session: bool = False,
        _retried: bool = False,
    ) -> Any:
        """One WDA round-trip; returns the response envelope's ``value``.

        ``session=True`` prefixes ``/session/<id>``. An ``invalid session id``
        answer resets the cached session and retries once — WDA invalidates
        sessions when the app under test crashes or is replaced.
        """
        url = path
        if session:
            url = f"/session/{self._ensure_session()}{path}"
        try:
            resp = self._http.request(method, url, json=payload)
        except httpx.HTTPError as e:
            raise WdaError(
                f"cannot reach WebDriverAgent at {self.url} ({e}); make sure "
                "WDA is running (USB real device: `iproxy 8100 8100`)",
                code="wda.unreachable",
            ) from e
        try:
            body = resp.json()
        except ValueError as e:
            if not resp.text:
                raise WdaError(
                    f"WDA returned an empty response for {method} {path}",
                    code="wda.empty_response",
                ) from e
            raise WdaError(
                f"WDA returned non-JSON for {method} {path}: {resp.text[:100]}",
                code="wda.bad_response",
            ) from e
        value = body.get("value")
        if isinstance(value, dict) and value.get("error"):
            error = str(value.get("error"))
            message = str(value.get("message") or error)
            if "invalid session id" in (error + message).lower() and not _retried:
                self._session_id = None
                return self._request(
                    method, path, payload, session=session, _retried=True
                )
            raise WdaError(f"WDA error: {message}", code=f"wda.{error}")
        # Session creation answers with a top-level sessionId (older WDA) or
        # inside value; surface both to _ensure_session via the envelope.
        if path == "/session" and method == "POST":
            return body
        return value

    def _ensure_session(self) -> str:
        if self._session_id:
            return self._session_id
        # WDA's current session (e.g. created by a previous app_launch, ours or
        # anyone else's) is advertised by /status.
        sid = self.status().get("sessionId")
        if not sid:
            body = self._request("POST", "/session", {"capabilities": {}})
            sid = body.get("sessionId") or (body.get("value") or {}).get("sessionId")
        if not sid:
            raise WdaError(
                "could not establish a WDA session", code="wda.no_session"
            )
        self._session_id = str(sid)
        return self._session_id

    # ---- device-level -------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """``GET /status``: WDA health blob, with the current sessionId inlined."""
        try:
            resp = self._http.get("/status")
            body = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            raise WdaError(
                f"cannot reach WebDriverAgent at {self.url} ({e}); make sure "
                "WDA is running (USB real device: `iproxy 8100 8100`)",
                code="wda.unreachable",
            ) from e
        value = body.get("value") or {}
        value["sessionId"] = body.get("sessionId")
        return dict(value)

    def is_ready(self) -> bool:
        """True when WDA answers /status (never raises)."""
        try:
            self.status()
            return True
        except QirabotError:
            return False

    def screenshot(self) -> bytes:
        """One PNG frame of the device screen."""
        value = self._request("GET", "/screenshot")
        return base64.b64decode(value or "")

    def app_launch(self, bundle_id: str) -> None:
        """Launch an app by creating a WDA session bound to its bundle id."""
        # Session creation fails on a locked device; best-effort unlock first
        # (mirrors facebook-wda's session()).
        try:
            if self._request("GET", "/wda/locked"):
                self._request("POST", "/wda/unlock")
        except QirabotError:
            pass
        capabilities = {
            "alwaysMatch": {
                "bundleId": bundle_id,
                "arguments": [],
                "environment": {},
                "shouldWaitForQuiescence": False,
            }
        }
        body = self._request(
            "POST",
            "/session",
            {
                "capabilities": capabilities,
                # pre-W3C WDA compatibility (same dual payload facebook-wda sends)
                "desiredCapabilities": capabilities["alwaysMatch"],
            },
        )
        sid = body.get("sessionId") or (body.get("value") or {}).get("sessionId")
        if sid:
            self._session_id = str(sid)

    def home(self) -> None:
        self._request("POST", "/wda/homescreen")

    def lock(self) -> None:
        self._request("POST", "/wda/lock")

    # ---- session-scoped interaction ----------------------------------------

    def window_size(self) -> tuple[int, int]:
        """Logical size in points (WDA reports points, not physical pixels)."""
        value = self._request("GET", "/window/size", session=True)
        return round(float(value["width"])), round(float(value["height"]))

    def tap(self, x: int, y: int) -> None:
        payload = {"x": x, "y": y}
        if self._legacy_tap:
            self._request("POST", "/wda/tap/0", payload, session=True)
            return
        try:
            self._request("POST", "/wda/tap", payload, session=True)
            self._legacy_tap = False
        except WdaError:
            if self._legacy_tap is not None:
                raise
            # WDA < 6.0 exposes /wda/tap/0 instead; remember which one worked.
            self._request("POST", "/wda/tap/0", payload, session=True)
            self._legacy_tap = True

    def double_tap(self, x: int, y: int) -> None:
        self._request("POST", "/wda/doubleTap", {"x": x, "y": y}, session=True)

    def tap_hold(self, x: int, y: int, duration: float = 1.0) -> None:
        self._request(
            "POST",
            "/wda/touchAndHold",
            {"x": x, "y": y, "duration": duration},
            session=True,
        )

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0) -> None:
        self._request(
            "POST",
            "/wda/dragfromtoforduration",
            {"fromX": x1, "fromY": y1, "toX": x2, "toY": y2, "duration": duration},
            session=True,
        )

    def send_keys(self, text: str) -> None:
        """Type into the focused element (unicode-native, no IME dance)."""
        self._request("POST", "/wda/keys", {"value": list(text)}, session=True)

    def press_button(self, name: str) -> None:
        """``home`` / ``volumeUp`` / ``volumeDown`` (the set WDA accepts)."""
        self._request("POST", "/wda/pressButton", {"name": name}, session=True)

    def close(self) -> None:
        """Release the HTTP connection pool (leaves the WDA session running)."""
        self._http.close()
