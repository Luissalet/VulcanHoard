"""The Faustus copilot inside Plato's Hoard: a Flask blueprint over the vendored Hoard Link pieces.

Hoard Link has no Flask helper, so the four routes are mounted here, but everything that is not Flask is the vendored
code: ``CopilotRelay`` (``hoard_link.copilot_stdlib``) finds Faustus (``PLATOS_FAUSTUS_URL`` / ``HOARD_FAUSTUS_URL`` +
``HOARD_FAUSTUS_TOKEN`` or the Hoard Link config), keeps the token and the stable data space id and forces the origin;
``multipart_context`` and ``inject_launcher`` (``hoard_link.copilot``) and ``LAUNCHER_JS`` (``hoard_link.copilot_launcher``)
are used as they are. The one thing added is ``FlaskRelay.forward``: a streaming answer as a Flask ``Response``.

* ``GET  /api/copilot/info``       the app, its stable data space id and where the panel comes from;
* ``GET  /api/copilot/panel.js``   the ``<faustus-chat>`` element, served by Faustus through this relay;
* ``POST /api/copilot/proxy``      every panel request (chat turns as Server-Sent Events, sessions, uploads); the Faustus
  token stays on the server, ``origin.app_id`` / ``data_space_id`` are forced and only the copilot paths of Faustus are
  reachable;
* ``GET  /api/copilot/launcher.js`` the floating "Faustus" button and side panel, added to every HTML page the app serves.

``HOARD_COPILOT=0`` turns the copilot off.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from flask import Blueprint, Response, jsonify, request

from vulcan.studio_native.plato import plato_family

APP_ID = "platos"
APP_NAME = "Plato's Hoard"
_FORWARD_UP = ("content-type", "x-odysseus-run-id", "x-faustus-idempotent-replay", "x-faustus-api-version")
_FORWARD_DOWN = ("x-faustus-client-version", "x-odysseus-run-id")
log = logging.getLogger("plato.copilot")

_stdlib = plato_family.vendored("copilot_stdlib")      # the relay for plain-Python hosts
_copilot = plato_family.vendored("copilot")            # inject_launcher, multipart_context
_launcher = plato_family.vendored("copilot_launcher")  # LAUNCHER_JS

if _copilot is not None:
    inject_launcher = _copilot.inject_launcher
    multipart_context = _copilot.multipart_context


def available() -> bool:
    """True when the vendored Hoard Link has the copilot modules."""
    return None not in (_stdlib, _copilot, _launcher)


def _path_allowed(path: Any) -> bool:
    """Only the copilot surface of Faustus is reachable through the relay.

    The vendored Hoard Link list is the single source (file uploads, dot-segment
    refusal); the inline list only serves an install without that module."""
    if _copilot is not None and hasattr(_copilot, "allowed_route"):
        return bool(_copilot.allowed_route(path))
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        return False
    route = urlsplit(path).path
    return route.startswith(("/api/hoard-copilot/", "/api/chat/", "/api/session/", "/api/sessions/")) \
        or route in ("/api/chat_stream", "/api/session", "/api/sessions")


def _error(status: int, message: str) -> Response:
    response = jsonify({"detail": message})
    response.status_code = status
    return response


if _stdlib is not None:
    class FlaskRelay(_stdlib.CopilotRelay):
        """The vendored relay (Faustus lookup, token, data space id, forced origin) with one Flask-specific method."""

        def forward(self, path: str, method: str = "GET", body: Optional[bytes] = None,
                    content_type: str = "application/json", extra: Optional[dict] = None) -> Response:
            """Faustus's answer as a Flask response, streamed chunk by chunk (a chat turn is a Server-Sent Events stream)."""
            try:
                base = self._locate()
            except ConnectionError as exc:
                return _error(503, str(exc))
            headers = {**self._auth(), **(extra or {}), "Content-Type": content_type}
            req = urllib.request.Request(base + path, data=body, headers=headers, method=method)
            try:
                upstream = self._opener.open(req, timeout=self.timeout)
            except urllib.error.HTTPError as exc:   # Faustus answered with an error status: pass it through untouched
                upstream = exc
            except (OSError, urllib.error.URLError):
                self._selected = None
                return _error(503, "Faustus copilot connection failed")

            def chunks():
                try:
                    while True:
                        chunk = upstream.read1(65536) if hasattr(upstream, "read1") else upstream.read(65536)
                        if not chunk:
                            break
                        yield chunk
                finally:
                    upstream.close()

            forwarded = {k: v for k, v in upstream.headers.items() if k.lower() in _FORWARD_UP}
            forwarded["X-Accel-Buffering"] = "no"
            forwarded["Cache-Control"] = "no-cache"
            status = upstream.code if isinstance(upstream, urllib.error.HTTPError) else upstream.status
            return Response(chunks(), status=status, headers=forwarded, direct_passthrough=True)


def make_blueprint(data_dir: Path | str, *, faustus_urls: Optional[list[str]] = None, token: Optional[str] = None) -> Blueprint:
    relay = FlaskRelay(APP_ID, data_dir, app_name=APP_NAME, faustus_urls=faustus_urls, token=token)
    bp = Blueprint("faustus_copilot", __name__, url_prefix="/api/copilot")
    bp.relay = relay  # type: ignore[attr-defined]

    @bp.get("/info")
    def info():
        try:
            base = relay._locate()
        except ConnectionError as exc:
            return _error(503, str(exc))
        return jsonify({**relay.origin, "app_name": APP_NAME, "panel_url": "/api/copilot/panel.js", "server": "/",
                        "tool_server_id": APP_ID, "settings_url": base + "/studio/settings"})

    @bp.get("/panel.js")
    def panel():
        return relay.forward("/api/hoard-copilot/panel.js")

    @bp.get("/launcher.js")
    def launcher():
        return Response(_launcher.LAUNCHER_JS.encode("utf-8"), mimetype="application/javascript", headers={"Cache-Control": "no-cache"})

    @bp.post("/proxy")
    def proxy():
        try:
            envelope = request.get_json(force=True, silent=True)
            if not isinstance(envelope, dict):
                raise ValueError("Invalid copilot transport request")
            path = envelope.get("path", "")
            if not _path_allowed(path):
                raise ValueError("Unknown copilot operation")
            body = None
            if "raw_body" in envelope:
                body = base64.b64decode(envelope["raw_body"], validate=True)
                body = multipart_context(body, envelope.get("content_type", ""), relay.origin)
            elif "body" in envelope:
                payload = envelope["body"]
                if isinstance(payload, dict):
                    for field in ("context", "hoard_context"):
                        if isinstance(payload.get(field), dict):
                            existing = payload[field].get("origin")
                            payload[field]["origin"] = {**(existing if isinstance(existing, dict) else {}), **relay.origin}
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            extra = {k: v for k, v in (envelope.get("headers") or {}).items() if k.lower() in _FORWARD_DOWN}
            return relay.forward(path, str(envelope.get("method", "GET")).upper(), body,
                                 envelope.get("content_type", "application/json"), extra)
        except (ValueError, TypeError, AttributeError):
            return _error(400, "Invalid copilot transport request")

    return bp


def install_copilot(app: Any, data_dir: Path | str, *, faustus_urls: Optional[list[str]] = None, token: Optional[str] = None) -> bool:
    """Mount the relay and add the launcher to every HTML page. ``HOARD_COPILOT=0`` leaves the app as it was."""
    if os.environ.get("HOARD_COPILOT", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    if not available():
        log.warning("the vendored Hoard Link has no copilot modules: the Faustus copilot is not installed")
        return False
    app.register_blueprint(make_blueprint(data_dir, faustus_urls=faustus_urls, token=token))

    @app.after_request
    def _add_launcher(response):
        if (request.method not in ("GET", "HEAD") or request.path.startswith("/api/") or response.status_code != 200
                or response.direct_passthrough or response.mimetype != "text/html" or response.headers.get("Content-Encoding")):
            return response
        response.set_data(inject_launcher(response.get_data()))
        response.headers.pop("ETag", None)
        return response

    return True
