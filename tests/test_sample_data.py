"""S1: sample data coverage (>=10 synthetic records, >=3 sites, every non-ok has a message)."""
from streamfhir.infrastructure.container import build_service


def test_sample_data_is_synthetic_and_covers_all_statuses():
    svc = build_service()
    records = svc.records.all()
    assert len(records) >= 10
    assert all(r.get("synthetic") is True for r in records)
    assert len({r.get("site_id") for r in records}) >= 3
    results = [svc.check_record(r).report for r in records]
    assert {r.status for r in results} == {"ok", "review", "blocked"}
    assert all(r.issues for r in results if r.status != "ok")


def test_sample_overview_levels():
    levels = {o.site.site_id: o.risk.level for o in build_service().site_overview()}
    assert set(levels.values()) == {"low", "moderate", "high"}
