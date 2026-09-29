"""Tiny stdlib web server: static UI + JSON API. Binds 127.0.0.1 by default."""
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..adapters.presenter import check_json, overview_json

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
ALLOW_LIVE = os.environ.get("STREAMFHIR_ALLOW_LIVE") == "1"


def make_server(service, host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the console quiet
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False, indent=1).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/api/sites":
                return self._send(200, overview_json(service.site_overview(), service.clock.now()))
            if path == "/api/records":
                return self._send(200, {"records": service.records.all()})
            name = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
            full = os.path.normpath(os.path.join(STATIC, name))
            if not full.startswith(STATIC) or not os.path.isfile(full):
                return self._send(404, {"error": "not found"})
            with open(full, "rb") as fh:
                self._send(200, fh.read(), (mimetypes.guess_type(full)[0] or "application/octet-stream") + "; charset=utf-8")

        def do_POST(self):
            path = self.path.split("?")[0]
            try:
                body = self._body()
            except ValueError:
                return self._send(400, {"error": "Body must be JSON."})
            if path == "/api/check":
                record = body.get("record")
                if not isinstance(record, dict):
                    return self._send(400, {"error": "Send {\"record\": {...}}."})
                return self._send(200, check_json(service.check_record(record)))
            if path == "/api/share":
                bundle = body.get("bundle")
                if not isinstance(bundle, dict):
                    return self._send(400, {"error": "Send {\"bundle\": {...}}."})
                live = bool(body.get("live")) and ALLOW_LIVE
                return self._send(200, service.share(bundle, live=live))
            if path == "/api/review":
                try:
                    return self._send(200, service.review(str(body.get("record_id")), body.get("decision")))
                except KeyError:
                    return self._send(404, {"error": "No such record."})
                except ValueError as err:
                    return self._send(400, {"error": str(err)})
            return self._send(404, {"error": "not found"})

    return ThreadingHTTPServer((host, port), Handler)
