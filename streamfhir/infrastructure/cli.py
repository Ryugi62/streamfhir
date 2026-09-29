"""Command line entry point: python3 -m streamfhir <command>."""
import argparse
import json
import os
import sys
import time

from ..adapters.fhir_mapper import (SID_SITE, capability_statement, conformance_bundle, conformance_resources,
                                    subscription_example)
from ..adapters.presenter import check_json, overview_json
from ..domain.risk import rules_table
from .container import ROOT, build_service
from .web import STATIC, make_server


def _record(service, record_id):
    for r in service.records.all():
        if r["record_id"] == record_id:
            return r
    sys.exit("No sample record %s" % record_id)


def _dump(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, ensure_ascii=False)
        fh.write("\n")


def build_static(svc, out):
    """Static, server-less demo (e.g. for GitHub Pages): precomputed API answers for the sample data."""
    os.makedirs(os.path.join(out, "api", "check"), exist_ok=True)
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as fh:
        html = fh.read()
    html = html.replace("<script>", "<script>window.STATIC_DEMO = true;</script>\n<script>", 1)
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(html)
    dataset = svc.records.dataset() if hasattr(svc.records, "dataset") else None
    _dump(os.path.join(out, "api", "sites.json"), overview_json(svc.site_overview(), svc.clock.now(), dataset))
    _dump(os.path.join(out, "api", "records.json"), {"records": svc.records.all()})
    for r in svc.records.all():
        _dump(os.path.join(out, "api", "check", "%s.json" % r["record_id"]), check_json(svc.check_record(r)))
    # precomputed single reviewer decisions, so Confirm/Reject also work in the static demo
    os.makedirs(os.path.join(out, "api", "review"), exist_ok=True)
    for rep in [svc.check_record(r).report for r in svc.records.all()]:
        if rep.status != "review":
            continue
        for decision, basis in (("confirm", "site-visit"), ("confirm", "photo-checked"), ("confirm", "lab-result"),
                                ("reject", None)):
            svc.reviews.set(rep.record_id, decision, basis)
            name = "%s-%s%s.json" % (rep.record_id, decision, "-" + basis if basis else "")
            _dump(os.path.join(out, "api", "review", name), overview_json(svc.site_overview(), svc.clock.now(), dataset))
            svc.reviews.set(rep.record_id, None)
    return len(svc.records.all())


def main(argv=None):
    p = argparse.ArgumentParser(prog="streamfhir", description="Citizen stream checks -> HL7 FHIR R4 + One Health warnings")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("serve", help="run the web UI (default)")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 inside a container")
    sub.add_parser("overview", help="print hazard level per site")
    sub.add_parser("rules", help="print the rules")
    c = sub.add_parser("check", help="validate + map one sample record")
    c.add_argument("record_id")
    e = sub.add_parser("export", help="write all Bundles to a folder")
    e.add_argument("--out", default=os.path.join(ROOT, "out"))
    sd = sub.add_parser("send", help="send one record's Bundle (dry run unless --live)")
    sd.add_argument("record_id")
    sd.add_argument("--live", action="store_true", help="really POST to the FHIR server (synthetic data only)")
    sd.add_argument("--flags", action="store_true", help="also send active Flags for high-hazard sites")
    sd.add_argument("--stand-down", action="store_true", help="with --flags: set the server's active Flag of now-calm sites to inactive")
    sd.add_argument("--review", choices=["confirm", "reject"], help="apply a reviewer decision to this record first")
    sd.add_argument("--basis", choices=["site-visit", "photo-checked", "lab-result"], help="basis for --review confirm")
    rv = sub.add_parser("review", help="confirm or reject a record that needs review, then show the overview")
    rv.add_argument("record_id")
    rv.add_argument("decision", choices=["confirm", "reject"])
    rv.add_argument("--basis", choices=["site-visit", "photo-checked", "lab-result"])
    v = sub.add_parser("validate-remote", help="ask the FHIR server to $validate one generated Observation")
    v.add_argument("record_id")
    v.add_argument("--location-id", help="server id of an existing Location to reference (e.g. from a live send)")
    v.add_argument("--with-profile", action="store_true", help="keep meta.profile (needs publish-conformance first)")
    v.add_argument("--code", help="validate the Observation carrying this code (e.g. nitrate), not the first one")
    pc = sub.add_parser("publish-conformance", help="PUT CodeSystems, ValueSet and profiles to the FHIR server (dry run unless --live)")
    pc.add_argument("--live", action="store_true")
    sub.add_parser("build-fhir", help="regenerate fhir/ conformance resources")
    bs = sub.add_parser("build-static", help="write a server-less demo to docs/demo (GitHub Pages ready)")
    bs.add_argument("--out", default=os.path.join(ROOT, "docs", "demo"))
    iw = sub.add_parser("import-wqp", help="convert a US Water Quality Portal Result+Station CSV snapshot into a StreamFHIR dataset")
    iw.add_argument("--dir", default=os.path.join(ROOT, "data", "real-wqp"), help="folder with results.csv and stations.csv")
    ih = sub.add_parser("import-hubeau", help="convert a Hub'Eau (France, Sandre) station + analysis JSON snapshot into a StreamFHIR dataset")
    ih.add_argument("--dir", default=os.path.join(ROOT, "data", "real-eu-toulouse"),
                    help="folder with stations.json, analyses.json, eea-sites.json and SOURCE.txt")
    ih.add_argument("--city", default="Toulouse area")
    ec = sub.add_parser("eea-coverage", help="summarise the EEA Waterbase snapshot near the five OneAquaHealth cities")
    ec.add_argument("--file", default=os.path.join(ROOT, "data", "eea-coverage", "waterbase-near-oah-cities.json"))
    bn = sub.add_parser("bench", help="measure validation + mapping throughput on this machine")
    bn.add_argument("-n", type=int, default=2000)
    p.add_argument("--data", default=None, help="dataset folder (default: data/ synthetic demo)")
    args = p.parse_args(argv)
    svc = build_service(args.data) if args.data else build_service()
    cmd = args.cmd or "serve"

    if cmd == "serve":
        port, host = getattr(args, "port", 8000), getattr(args, "host", "127.0.0.1")
        httpd = make_server(svc, host, port)
        print("StreamFHIR running at http://%s:%d  (Ctrl+C to stop)" % (host, port))
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    elif cmd == "overview":
        print("as of", svc.clock.now().isoformat())
        for o in svc.site_overview():
            r = o.risk
            print("%-11s %-9s hazard %2d (corroborated %2d) %s  condition=%s(%d)  rules=%s  flag=%s" % (
                o.site.site_id, r.level.upper() if r.hazard_assessed else "NOT-ASSESSED", r.hazard_points, r.confirmed_hazard_points, r.lane_points,
                r.condition, r.condition_points, [f.rule_id + ("" if f.corroborated else "?") for f in r.fired],
                bool(o.flag_bundle)))
    elif cmd == "rules":
        for r in rules_table():
            print("%s [%s] %-45s %s\n    when:   %s\n    why:    %s\n    advice: %s" % (
                r["id"], r["kind"], r["title"], r["points"], r["when"], r["why"], r["advice"]))
    elif cmd == "check":
        print(json.dumps(check_json(svc.check_record(_record(svc, args.record_id))), indent=1, ensure_ascii=False))
    elif cmd == "export":
        os.makedirs(args.out, exist_ok=True)
        n = 0
        for r in svc.records.all():
            b = svc.check_record(r).bundle
            if b:
                _dump(os.path.join(args.out, "bundle-%s.json" % r["record_id"]), b)
                n += 1
        for o in svc.site_overview():
            if o.flag_bundle:
                _dump(os.path.join(args.out, "flag-%s.json" % o.site.site_id), o.flag_bundle)
                n += 1
        print("wrote %d bundles to %s" % (n, args.out))
    elif cmd == "send":
        if args.review:
            print(svc.review(args.record_id, args.review, args.basis))
        res = svc.check_record(_record(svc, args.record_id))
        if res.bundle is None:
            sys.exit("Record %s is blocked: %s" % (args.record_id, [i.message for i in res.report.issues]))
        print(json.dumps(svc.share(res.bundle, live=args.live), indent=1))
        if args.flags:
            for b in svc.flag_bundles(include_stand_down=args.stand_down):
                flag = [e["resource"] for e in b["entry"] if e["resource"]["resourceType"] == "Flag"][0]
                print(flag["identifier"][0]["value"], flag["status"], json.dumps(svc.share(b, live=args.live), indent=1))
    elif cmd == "review":
        print(svc.review(args.record_id, args.decision, args.basis))
        for o in svc.site_overview():
            print("%-11s %-9s hazard %2d (corroborated %2d) flag=%s" % (
                o.site.site_id, o.risk.level.upper(), o.risk.hazard_points, o.risk.confirmed_hazard_points, bool(o.flag_bundle)))
    elif cmd == "validate-remote":
        res = svc.check_record(_record(svc, args.record_id))
        obs = [e["resource"] for e in res.bundle["entry"] if e["resource"]["resourceType"] == "Observation"
               and "hasMember" not in e["resource"]
               and (not args.code or args.code in {c["code"] for c in e["resource"]["code"]["coding"]})][0]
        subject = ({"reference": "Location/%s" % args.location_id} if args.location_id else
                   {"identifier": {"system": SID_SITE, "value": res.report.assessment.site_id}})
        obs = dict(obs, subject=dict(subject, display=obs["subject"]["display"]))
        obs.pop("derivedFrom", None)
        if not args.with_profile:
            obs["meta"] = {k: v for k, v in obs["meta"].items() if k != "profile"}
        print(json.dumps(svc.server.validate(obs), indent=1))
    elif cmd == "publish-conformance":
        print(json.dumps(svc.share(conformance_bundle(), live=args.live), indent=1))
    elif cmd == "build-fhir":
        out = os.path.join(ROOT, "fhir")
        for name in os.listdir(out):
            if name.endswith(".json"):
                os.remove(os.path.join(out, name))
        items = [("%s-%s.json" % (r["resourceType"], r["id"]), r) for r in conformance_resources()]
        items += [("CapabilityStatement-streamfhir-client-requirements.json", capability_statement()),
                  ("Subscription-example-safety-flags.json", subscription_example())]
        for name, res in items:
            _dump(os.path.join(out, name), res)
        print("wrote %d files to %s" % (len(items), out))
    elif cmd == "build-static":
        n = build_static(svc, args.out)
        print("wrote static demo with %d precomputed checks to %s" % (n, args.out))
    elif cmd == "import-wqp":
        from ..adapters.wqp_importer import parse_results, parse_stations
        with open(os.path.join(args.dir, "stations.csv"), encoding="utf-8") as fh:
            sites = parse_stations(fh.read())
        with open(os.path.join(args.dir, "results.csv"), encoding="utf-8") as fh:
            records, stats = parse_results(fh.read(), {x["site_id"]: x for x in sites})
        used = {r["site_id"] for r in records}
        latest = max(r["observed_at"][:10] for r in records)
        with open(os.path.join(args.dir, "SOURCE.txt"), encoding="utf-8") as fh:
            source = fh.read().strip().splitlines()
        _dump(os.path.join(args.dir, "sites.json"), {"_note": "REAL public stations from the US Water Quality Portal (not citizen science).",
                                                      "sites": [x for x in sites if x["site_id"] in used]})
        _dump(os.path.join(args.dir, "assessments.json"), {
            "_note": "REAL public monitoring data imported from the US Water Quality Portal; source query and retrieval time below.",
            "dataset": {"synthetic": False, "observer_kind": "organisation", "source_query": source[0],
                        "retrieved": source[1] if len(source) > 1 else None, "import_stats": stats},
            "demo_as_of": latest + "T23:59:59+00:00", "records": records})
        print(json.dumps(dict(stats, sites=len(used)), indent=1))
    elif cmd == "import-hubeau":
        from ..adapters.hubeau_importer import parse_analyses, parse_stations

        def load(name):
            with open(os.path.join(args.dir, name), encoding="utf-8") as fh:
                return json.load(fh)
        eu = {x["monitoringSiteIdentifier"] for x in load("eea-sites.json")["sites"]}
        sites = parse_stations(load("stations.json"), city=args.city, eu_site_codes=eu)
        records, stats = parse_analyses(load("analyses.json"), {x["site_id"]: x for x in sites})
        used = {r["site_id"] for r in records}
        latest = max(r["observed_at"][:10] for r in records)
        with open(os.path.join(args.dir, "SOURCE.txt"), encoding="utf-8") as fh:
            source = fh.read().strip().splitlines()
        stats["sites"] = len(used)
        stats["sites_with_eu_code"] = sum(1 for x in sites if x["site_id"] in used and len(x["identifiers"]) > 1)
        _dump(os.path.join(args.dir, "sites.json"), {
            "_note": "REAL public French river monitoring stations (Hub'Eau / Sandre), not citizen science.",
            "sites": [x for x in sites if x["site_id"] in used]})
        _dump(os.path.join(args.dir, "assessments.json"), {
            "_note": "REAL public monitoring data imported from Hub'Eau qualite_rivieres (France); source queries and retrieval time below.",
            "dataset": {"synthetic": False, "observer_kind": "organisation", "source_query": source[1] if len(source) > 1 else source[0],
                        "retrieved": source[-1], "import_stats": stats, "label": "Real EU data: %s rivers (Hub'Eau, France)" % args.city,
                        "evaluate_each_site_at_its_latest_visit": True},
            "demo_as_of": latest + "T23:59:59+01:00", "records": records})
        print(json.dumps(stats, indent=1))
    elif cmd == "eea-coverage":
        from ..adapters.waterbase_importer import coverage
        with open(args.file, encoding="utf-8") as fh:
            data = json.load(fh)
        print(json.dumps({c: dict(coverage(v["rows"]), surface_sites=len(v["surface_sites"])) for c, v in data["cities"].items()}, indent=1))
    elif cmd == "bench":
        recs = [r for r in svc.records.all() if svc.check_record(r).bundle]
        t = time.perf_counter()
        for i in range(args.n):
            svc.check_record(recs[i % len(recs)])
        dt = time.perf_counter() - t
        print("validated + mapped %d records in %.2f s = %.0f records/s (single thread, this machine)" % (
            args.n, dt, args.n / dt))
