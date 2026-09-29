"""Adapter: FHIR REST client (HAPI FHIR R4 public test server by default).

Network access only happens through ``transport`` so tests can inject a fake.
"""
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple

DEFAULT_BASE = "https://hapi.fhir.org/baseR4"
FHIR_JSON = "application/fhir+json"
Transport = Callable[[str, str, Optional[bytes], Dict[str, str]], Tuple[int, bytes]]


def urllib_transport(method: str, url: str, body: Optional[bytes], headers: Dict[str, str]) -> Tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


class HapiFhirServer:
    def __init__(self, base_url: str = DEFAULT_BASE, transport: Optional[Transport] = None):
        self.base_url = base_url.rstrip("/")
        self.transport = transport or urllib_transport
        self.headers = {"Content-Type": FHIR_JSON, "Accept": FHIR_JSON}

    def post_transaction(self, bundle: Dict[str, Any]) -> Dict[str, Any]:
        status, raw = self.transport("POST", self.base_url, json.dumps(bundle).encode("utf-8"), dict(self.headers))
        body = json.loads(raw.decode("utf-8") or "{}")
        locations = [e.get("response", {}).get("location") for e in body.get("entry", [])]
        return {"status": status, "server": self.base_url, "locations": [l for l in locations if l],
                "outcome": body if body.get("resourceType") == "OperationOutcome" else None}

    def find_flag(self, site_id: str) -> Optional[Dict[str, Any]]:
        from .fhir_mapper import SID_FLAG
        url = "%s/Flag?identifier=%s" % (self.base_url, urllib.parse.quote("%s|%s" % (SID_FLAG, site_id), safe=""))
        status, raw = self.transport("GET", url, None, dict(self.headers))
        if status != 200:
            return None
        entries = json.loads(raw.decode("utf-8") or "{}").get("entry", [])
        return entries[0]["resource"] if entries else None

    def validate(self, resource: Dict[str, Any]) -> Dict[str, Any]:
        url = "%s/%s/$validate" % (self.base_url, resource["resourceType"])
        status, raw = self.transport("POST", url, json.dumps(resource).encode("utf-8"), dict(self.headers))
        body = json.loads(raw.decode("utf-8") or "{}")
        issues = [{"severity": i.get("severity"), "text": i.get("diagnostics") or i.get("details", {}).get("text", "")}
                  for i in body.get("issue", [])]
        return {"status": status, "issues": issues}
