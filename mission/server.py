"""Tiny loopback HTTP server: one page, one JSON snapshot, one SSE tick.

Standard library only — no fastapi, no uvicorn, no build step. Binds 127.0.0.1
and never to a public interface: this view exposes the agent's own history.
"""

from __future__ import annotations

import json
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import jobs, store

HOST = "127.0.0.1"
DEFAULT_PORT = 7337
WEB = Path(__file__).parent / "web"

_state: dict = {"server": None, "thread": None, "port": None}
_lock = threading.Lock()

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "mission/0.1"

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    def _send(self, status: int, body: bytes, ctype: str = "application/json", extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, payload, status: int = 200):
        self._send(status, json.dumps(payload, default=str).encode("utf-8"))

    def do_GET(self):  # noqa: N802
        try:
            return self._route()
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:  # never drop the connection silently
            import traceback

            line = "--- %s %s\n%s\n" % (time.time(), self.path, traceback.format_exc())
            try:
                with open(store.home() / "mission-error.log", "a", encoding="utf-8") as fh:
                    fh.write(line)
            except OSError:
                pass
            try:
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)
            except Exception:
                pass

    def _route(self):
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)

        if route == "/api/stream":
            return self._stream()
        if route == "/api/snapshot":
            hours = int(query.get("hours", ["24"])[0])
            return self._json(store.snapshot(hours=hours))
        if route == "/api/search":
            return self._json({"hits": store.search(query.get("q", [""])[0])})
        if route == "/api/waste":
            return self._json({"sessions": store.waste()})
        if route == "/api/jobs":
            return self._json({"jobs": jobs.listing()})
        if route == "/api/events":
            since = int(query.get("since", ["0"])[0] or 0)
            return self._json({"events": store.events(since)})
        if route.startswith("/api/session/"):
            return self._json(store.session_detail(route.rsplit("/", 1)[-1]))

        name = "index.html" if route in ("/", "") else route.lstrip("/")
        if "/" in name or name.startswith("."):
            return self._json({"error": "not found"}, 404)
        path = WEB / name
        if not path.exists():
            return self._json({"error": "not found"}, 404)
        self._send(200, path.read_bytes(), CONTENT_TYPES.get(path.suffix, "application/octet-stream"))

    def do_POST(self):  # noqa: N802
        try:
            return self._route_post()
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            try:
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)
            except Exception:
                pass

    def _same_origin(self) -> bool:
        origin = (self.headers.get("Origin") or self.headers.get("Referer") or "").lower()
        if not origin:
            return self.headers.get("X-Mission") == "1"
        return "localhost" in origin or "127.0.0.1" in origin

    def _body(self) -> dict:
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if size <= 0 or size > 200_000:
            return {}
        try:
            return json.loads(self.rfile.read(size).decode("utf-8")) or {}
        except (ValueError, UnicodeDecodeError):
            return {}

    def _route_post(self):
        if not self._same_origin():
            return self._json({"error": "bad origin"}, 403)
        route = urlparse(self.path).path
        body = self._body()

        if route == "/api/run":
            prompt = str(body.get("prompt") or "")
            session = str(body.get("session") or "") or None
            profile = str(body.get("profile") or "") or None
            cwd = str(body.get("cwd") or "") or None
            result = jobs.start(prompt, session_id=session, profile=profile, cwd=cwd)
            return self._json(result, 200 if result.get("ok") else 400)
        if route == "/api/stop":
            result = jobs.stop(str(body.get("job") or ""))
            return self._json(result, 200 if result.get("ok") else 400)
        return self._json({"error": "not found"}, 404)

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        last = None
        try:
            while True:
                snap = store.snapshot(hours=24)
                live = [s["id"] for s in snap["sessions"] if s["live"]]
                fingerprint = (
                    snap["totals"]["sessions"],
                    snap["totals"]["tokens"],
                    tuple(live),
                    round(snap["now"], -0),
                )
                if fingerprint != last:
                    payload = {
                        "totals": snap["totals"],
                        "live": live,
                        "now": snap["now"],
                        "timeline": snap["timeline"][-1:] or [],
                    }
                    self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    last = fingerprint
                time.sleep(2.0)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


def start(port: int = DEFAULT_PORT, open_browser: bool = False) -> dict:
    with _lock:
        if _state["server"] is not None:
            url = f"http://{HOST}:{_state['port']}/"
            if open_browser:
                webbrowser.open(url)
            return {"ok": True, "url": url, "already_running": True}

        server = ThreadingHTTPServer((HOST, port), Handler)
        server.daemon_threads = True
        bound = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, name="mission-http", daemon=True)
        thread.start()
        _state.update({"server": server, "thread": thread, "port": bound})
        url = f"http://{HOST}:{bound}/"
        if open_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        return {"ok": True, "url": url, "already_running": False}


def stop() -> dict:
    with _lock:
        server = _state.get("server")
        if server is None:
            return {"ok": True, "was_running": False}
        server.shutdown()
        server.server_close()
        _state.update({"server": None, "thread": None, "port": None})
        return {"ok": True, "was_running": True}


def running() -> bool:
    return _state.get("server") is not None
