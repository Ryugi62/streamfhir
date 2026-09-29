"""UC-1..UC-3 with fakes (no network), AC-13, AC-14."""
import json

from streamfhir.adapters.fhir_mapper import FhirMapper
from streamfhir.adapters.hapi_client import HapiFhirServer
from streamfhir.application.use_cases import StreamFhirService
from tests.conftest import NOW, SITES, make_record


class FakeSites:
    def all(self):
        return dict(SITES)


class FakeRecords:
    def __init__(self, records):
        self.records = records

    def all(self):
        return list(self.records)


class SpyServer:
    def __init__(self, flags=None):
        self.calls = []
        self.flags = flags or {}

    def find_flag(self, site_id):
        return self.flags.get(site_id)

    def post_transaction(self, bundle):
        self.calls.append(bundle)
        return {"status": 200, "locations": ["Location/1/_history/1"]}


class FixedClock:
    def now(self):
        return NOW


def service(records=(), server=None):
    return StreamFhirService(FakeSites(), FakeRecords(records), FhirMapper(), server or SpyServer(), FixedClock())


def test_uc1_check_record_returns_report_and_bundle():
    result = service().check_record(make_record())
    assert result.report.status == "ok"
    assert result.bundle["type"] == "transaction"


def test_uc1_blocked_record_returns_no_bundle():
    result = service().check_record(make_record(values={"ph": 15}))
    assert result.report.status == "blocked"
    assert result.bundle is None


def test_uc2_overview_has_one_entry_per_site_and_flags_only_high():
    recs = [make_record(record_id="A", values={"odour": "sewage", "people-contact": True}),
            make_record(record_id="B", site_id="S-OTHER", lat=40.1985, lon=-8.4120)]
    overview = service(recs).site_overview()
    by_site = {o.site.site_id: o for o in overview}
    assert set(by_site) == {"S-TEST", "S-OTHER"}
    assert by_site["S-TEST"].risk.level == "high" and by_site["S-TEST"].flag_bundle is not None
    assert by_site["S-OTHER"].risk.level == "low" and by_site["S-OTHER"].flag_bundle is None


def test_ac13_dry_run_is_default_and_makes_no_call():
    spy = SpyServer()
    svc = service(server=spy)
    bundle = svc.check_record(make_record()).bundle
    out = svc.share(bundle)
    assert spy.calls == []
    assert out["mode"] == "dry-run"
    assert out["resource_counts"]["Observation"] == 18


def test_ac14_live_share_returns_server_locations_via_fake_transport():
    seen = {}

    def transport(method, url, body, headers):
        seen.update(method=method, url=url, headers=headers)
        resp = {"resourceType": "Bundle", "type": "transaction-response",
                "entry": [{"response": {"status": "201 Created", "location": "Observation/42/_history/1"}}]}
        return 200, json.dumps(resp).encode()

    server = HapiFhirServer("https://fhir.example.test/baseR4", transport=transport)
    svc = service(server=server)
    out = svc.share(svc.check_record(make_record()).bundle, live=True)
    assert out["mode"] == "live"
    assert out["locations"] == ["Observation/42/_history/1"]
    assert seen["method"] == "POST" and seen["url"] == "https://fhir.example.test/baseR4"
    assert seen["headers"]["Content-Type"] == "application/fhir+json"


def test_uc4_review_confirm_turns_verify_into_high_with_flag():
    rec = make_record(record_id="R-1", photos=[], values={"surface": "algal-scum", "water-temperature": 24.0,
                                                           "animal-contact": True})
    svc = service([rec])
    before = {o.site.site_id: o for o in svc.site_overview()}["S-TEST"]
    assert before.risk.level == "verify" and before.flag_bundle is None
    try:
        svc.review("R-1", "confirm")
        assert False, "a confirmation without a basis must be refused"
    except ValueError:
        pass
    svc.review("R-1", "confirm", "site-visit")
    after = {o.site.site_id: o for o in svc.site_overview()}["S-TEST"]
    assert after.risk.level == "high" and after.flag_bundle is not None
    svc.review("R-1", "reject")
    assert {o.site.site_id: o for o in svc.site_overview()}["S-TEST"].risk.level == "low"


def test_uc4_only_review_records_can_be_decided():
    svc = service([make_record(record_id="OK-1")])
    try:
        svc.review("OK-1", "confirm", "site-visit")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_new_record_is_stamped_with_service_clock_not_device_clock():
    rec = make_record(observed_at="2031-01-01T00:00:00+00:00")  # a device clock far ahead of the demo date
    assert service().check_record(rec).report.status == "blocked"
    stamped = service().check_record(rec, stamp_now=True)
    assert stamped.report.status == "ok" and stamped.report.assessment.observed_at == NOW


def test_stand_down_only_for_sites_with_an_active_flag_and_keeps_start():
    active = {"status": "active", "period": {"start": "2026-09-10T08:00:00+00:00"}}
    svc = service([make_record(record_id="B", site_id="S-OTHER", lat=40.1985, lon=-8.4120)],
                  server=SpyServer(flags={"S-OTHER": active}))
    bundles = svc.flag_bundles(include_stand_down=True)
    flags = [e["resource"] for b in bundles for e in b["entry"] if e["resource"]["resourceType"] == "Flag"]
    assert [f["status"] for f in flags] == ["inactive"]           # S-TEST has no Flag on the server -> nothing
    assert flags[0]["period"]["start"] == "2026-09-10T08:00:00+00:00"
