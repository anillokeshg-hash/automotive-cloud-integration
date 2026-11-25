"""The cloud HTTP API (standard library only - nothing to install).

    POST /api/telemetry                    vehicle -> cloud: send a reading
    GET  /api/vehicles                     latest status of every vehicle
    GET  /api/vehicles/<vin>/telemetry     recent history (?limit=50)
    GET  /api/alerts                       alerts (?open=1&vin=...&limit=...)
    POST /api/vehicles/<vin>/commands      queue a remote command {"command": "flash_lights"}
    GET  /health                           liveness check (no key needed)
    GET  /                                 dashboard page (no key needed; its data calls use the key)

All /api/* calls need the header   X-API-Key: <key>

Run:  python server.py
"""

import hmac
import json
import logging
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import config
from service import CloudService
from storage import Store

log = logging.getLogger("server")

MAX_BODY_BYTES = 64 * 1024
DASHBOARD = Path(__file__).with_name("dashboard.html")
VIN_PATH = r"([A-Za-z0-9-]{5,32})"


class Handler(BaseHTTPRequestHandler):
    service: CloudService = None  # set by make_server
    api_key: str = ""

    def log_message(self, fmt, *args):  # route access logs through logging
        log.info("%s %s", self.address_string(), fmt % args)

    # ---- helpers ----
    def _json(self, status: int, body) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        supplied = self.headers.get("X-API-Key", "")
        return hmac.compare_digest(supplied.encode(), self.api_key.encode())

    def _read_json(self):
        """Return (data, error_message)."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None, "invalid Content-Length"
        if length > MAX_BODY_BYTES:
            return None, "body too large"
        try:
            return json.loads(self.rfile.read(length) or b"null"), None
        except ValueError:
            return None, "body is not valid JSON"

    @staticmethod
    def _int_param(query: dict, name: str, default: int) -> int:
        try:
            return int(query.get(name, [default])[0])
        except ValueError:
            return default

    # ---- routing ----
    def do_GET(self):
        url = urlparse(self.path)
        path, query = url.path, parse_qs(url.query)
        try:
            if path == "/":
                return self._send_dashboard()
            if path == "/health":
                return self._json(200, {"status": "ok"})
            if not path.startswith("/api/"):
                return self._json(404, {"error": "not found"})
            if not self._authorized():
                return self._json(401, {"error": "missing or invalid X-API-Key"})

            if path == "/api/vehicles":
                return self._json(200, self.service.fleet())
            if path == "/api/alerts":
                only_open = query.get("open", ["0"])[0] in ("1", "true")
                vin = query.get("vin", [None])[0]
                return self._json(200, self.service.alerts(only_open, vin, self._int_param(query, "limit", 100)))
            match = re.fullmatch(rf"/api/vehicles/{VIN_PATH}/telemetry", path)
            if match:
                return self._json(200, self.service.history(match.group(1), self._int_param(query, "limit", 50)))
            return self._json(404, {"error": "not found"})
        except ValueError as err:
            return self._json(400, {"error": str(err)})
        except Exception:
            log.exception("Unhandled error")
            return self._json(500, {"error": "internal server error"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if not path.startswith("/api/"):
                return self._json(404, {"error": "not found"})
            if not self._authorized():
                return self._json(401, {"error": "missing or invalid X-API-Key"})
            body, error = self._read_json()
            if error:
                return self._json(413 if "large" in error else 400, {"error": error})

            if path == "/api/telemetry":
                return self._json(200, self.service.ingest(body))
            match = re.fullmatch(rf"/api/vehicles/{VIN_PATH}/commands", path)
            if match:
                if not isinstance(body, dict):
                    return self._json(400, {"error": "body must be a JSON object"})
                command_id = self.service.queue_command(match.group(1), str(body.get("command", "")))
                return self._json(202, {"queued": True, "command_id": command_id})
            return self._json(404, {"error": "not found"})
        except ValueError as err:
            return self._json(400, {"error": str(err)})
        except LookupError as err:
            return self._json(404, {"error": str(err)})
        except Exception:
            log.exception("Unhandled error")
            return self._json(500, {"error": "internal server error"})

    def _send_dashboard(self) -> None:
        data = DASHBOARD.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)


def make_server(service: CloudService, api_key: str, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """Create (but do not start) the server. port=0 picks any free port."""
    handler = type("BoundHandler", (Handler,), {"service": service, "api_key": api_key})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    if config.API_KEY == "dev-key":
        log.warning(config.DEFAULT_KEY_WARNING)
    server = make_server(CloudService(Store(config.DB_PATH)), config.API_KEY, config.HOST, config.PORT)
    log.info("Cloud API listening on http://%s:%s  (dashboard: /)", config.HOST, server.server_address[1])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
