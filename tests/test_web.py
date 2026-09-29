"""AC-16: web API smoke test on 127.0.0.1 ephemeral port (local only, no internet)."""
import json
import threading
import urllib.request

from streamfhir.infrastructure.container import build_service
from streamfhir.infrastructure.web import make_server
from tests.conftest import make_record


def _serve():
    httpd = make_server(build_service(), "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, "http://127.0.0.1:%d" % httpd.server_address[1]


def test_ac16_post_check_returns_status_and_bundle():
    httpd, base = _serve()
    try:
        req = urllib.request.Request(base + "/api/check", data=json.dumps({"record": make_record(site_id="S-ALDER-UP", lat=40.2150, lon=-8.4050)}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        body = json.loads(urllib.request.urlopen(req, timeout=5).read())
        assert body["report"]["status"] in ("ok", "review")
        assert body["bundle"]["resourceType"] == "Bundle"
    finally:
        httpd.shutdown()


def test_sites_endpoint_lists_all_sample_sites_and_index_is_served():
    httpd, base = _serve()
    try:
        sites = json.loads(urllib.request.urlopen(base + "/api/sites", timeout=5).read())
        assert len(sites["sites"]) >= 3
        assert {s["risk"]["level"] for s in sites["sites"]} >= {"high", "low"}
        html = urllib.request.urlopen(base + "/", timeout=5).read().decode()
        assert "<meta name=\"viewport\"" in html
    finally:
        httpd.shutdown()


def test_web_share_is_dry_run_even_if_live_requested_without_env():
    httpd, base = _serve()
    try:
        svc_bundle = build_service().check_record(make_record(site_id="S-ALDER-UP", lat=40.2150, lon=-8.4050)).bundle
        req = urllib.request.Request(base + "/api/share", data=json.dumps({"bundle": svc_bundle, "live": True}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        body = json.loads(urllib.request.urlopen(req, timeout=5).read())
        assert body["mode"] == "dry-run"
    finally:
        httpd.shutdown()
