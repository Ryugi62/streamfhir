"""Command line entry point: python3 -m streamfhir <command>."""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

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
    captured = os.path.join(os.path.dirname(svc.records.path), "interop-query.json") if hasattr(svc.records, "path") else ""
    if captured and os.path.exists(captured):
        with open(captured, encoding="utf-8") as fh:
            _dump(os.path.join(out, "api", "interop.json"), {k: v for k, v in json.load(fh).items() if not k.startswith("raw_")})
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


SYNTHETIC_NOTE = ("SYNTHETIC citizen check created for an interoperability demo at a real station; "
                  "not a real volunteer report.")


def interop_bundles(svc, site_id: str):
    """For `interop-demo`: the latest real agency sampling at a real station, two clearly SYNTHETIC citizen checks
    at the same station (two observers, scum + dogs in the water, photos kept for review) and the Flag they raise."""
    from datetime import timedelta
    from ..adapters.fhir_mapper import FhirMapper
    from ..domain.risk import evaluate_site
    from ..domain.validation import parse_time, validate_record
    recs = sorted([r for r in svc.records.all() if r["site_id"] == site_id and "nitrate" in r["values"]],
                  key=lambda r: r["observed_at"])
    agency_raw = recs[-1]
    sites = svc.sites.all()
    site = sites[site_id]
    t0 = parse_time(agency_raw["observed_at"])
    citizens = []
    for n, (who, hours) in enumerate((("obs-demo-a", 3), ("obs-demo-b", 27)), 1):
        citizens.append({"record_id": "SYN-CITIZEN-%s-%d" % (site_id, n), "synthetic": True, "site_id": site_id,
                         "observed_at": (t0 + timedelta(hours=hours)).isoformat(), "observer": who,
                         "lat": site.lat, "lon": site.lon, "gps_accuracy_m": 8,
                         "photos": ["https://example.org/demo-photos/synthetic-%s-%d.jpg" % (site_id.lower(), n)],
                         "values": {"nitrate": 50, "nitrate-basis": "as-NO3", "water-colour": "green",
                                    "surface": "algal-scum", "odour": "none", "people-contact": False,
                                    "animal-contact": True}})
    now = svc.clock.now()
    agency_rep = validate_record(agency_raw, sites, now)
    cit_reps = [validate_record(c, sites, now) for c in citizens]
    test_mapper = FhirMapper(test_data=True, pseudonymous=True)
    cit_bundles = [test_mapper.assessment_bundle(r, site) for r in cit_reps]
    for b in cit_bundles:
        for e in b["entry"]:
            if e["resource"]["resourceType"] in ("Observation", "Media"):
                e["resource"]["note"] = [{"text": SYNTHETIC_NOTE}] + e["resource"].get("note", [])
    risk = evaluate_site(site_id, [agency_rep] + cit_reps, as_of=max(r.assessment.observed_at for r in cit_reps))
    flag = test_mapper.risk_bundle(risk, site)
    if flag:
        for e in flag["entry"]:
            if e["resource"]["resourceType"] == "Flag":
                e["resource"]["code"]["text"] = "SYNTHETIC DEMO - " + e["resource"]["code"]["text"]
    return svc.check_record(agency_raw).bundle, cit_bundles, flag


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
    iff = sub.add_parser("import-fww", help="convert FreshWater Watch (Earthwatch) citizen-science snapshots into a StreamFHIR dataset")
    iff.add_argument("--dir", default=os.path.join(ROOT, "data", "real-fww"), help="folder with fww-<City>.json and SOURCE.txt")
    for name, city in (("import-ghent", "Ghent (VMM)"), ("import-benevento", "Benevento (ARPA Campania)")):
        sub.add_parser(name, help="convert the %s river-quality snapshot into a StreamFHIR dataset" % city)
    ec = sub.add_parser("eea-coverage", help="summarise the EEA Waterbase snapshot near the five OneAquaHealth cities")
    ec.add_argument("--file", default=os.path.join(ROOT, "data", "eea-coverage", "waterbase-near-oah-cities.json"))
    io = sub.add_parser("interop-demo", help="send one real agency sampling + one synthetic citizen check at the same station, "
                                            "then query both by the EEA nitrate code (dry run unless --live)")
    io.add_argument("--site", default="FR-05157550")
    io.add_argument("--live", action="store_true")
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
    elif cmd == "import-fww":
        from ..adapters.fww_importer import parse_records, parse_sites
        regions = {"Coimbra": "Central Portugal", "Toulouse": "Toulouse area"}
        zones = {"Coimbra": "Europe/Lisbon", "Toulouse": "Europe/Paris"}
        sites, records, stats = [], [], {}
        for name in sorted(os.listdir(args.dir)):
            if not (name.startswith("fww-") and name.endswith(".json")):
                continue
            city = name[4:-5]
            with open(os.path.join(args.dir, name), encoding="utf-8") as fh:
                payload = json.load(fh)
            ss = parse_sites(payload, region=regions.get(city, city))
            rs, st = parse_records(payload, {x["site_id"]: x for x in ss}, tz=zones.get(city, "UTC"))
            sites += ss; records += rs; stats[city] = st
        with open(os.path.join(args.dir, "SOURCE.txt"), encoding="utf-8") as fh:
            source = fh.read().strip().splitlines()
        latest = max(r["observed_at"][:10] for r in records)
        _dump(os.path.join(args.dir, "sites.json"), {"_note": "REAL citizen-science sites (Earthwatch Europe FreshWater Watch).",
                                                      "sites": sites})
        _dump(os.path.join(args.dir, "assessments.json"), {
            "_note": "REAL citizen-science records from Earthwatch Europe FreshWater Watch (public ArcGIS view); source and licence note below.",
            "dataset": {"synthetic": False, "observer_kind": "citizen", "source_query": source[1] if len(source) > 1 else "",
                        "retrieved": source[-2] if len(source) > 2 else "", "import_stats": stats,
                        "label": "Real citizen science: FreshWater Watch volunteers near Toulouse and Coimbra",
                        "banner": "Real volunteer observations from Earthwatch Europe's FreshWater Watch (open access, no formal licence; "
                                  "attribution Earthwatch Europe) within about 50 km of Toulouse and in central Portugal (Aveiro, Figueira da "
                                  "Foz; 40-55 km from Coimbra): one volunteer campaign in March 2023 plus a few other visits, single visits "
                                  "per site. Only fields whose meaning matches StreamFHIR indicators are scored; kit bands are notes. "
                                  "For real citizen data a photo counts only after a reviewer's photo check. Each site is shown as of its "
                                  "own visit date.",
                        "photo_needs_review": True, "evaluate_each_site_at_its_latest_visit": True},
            "demo_as_of": latest + "T23:59:59+00:00", "records": records})
        print(json.dumps(dict(stats, sites=len(sites), records=len(records)), indent=1))
    elif cmd in ("import-ghent", "import-benevento"):
        from datetime import date, timedelta
        from ..adapters import national_feeds as nf
        if cmd == "import-ghent":
            folder, city = os.path.join(ROOT, "data", "real-eu-ghent"), "Ghent"
            stations = [{"code": "OW172100", "name": "Bovenschelde in Gent", "lat": 51.0016, "lon": 3.72403},
                        {"code": "OW571900", "name": "Leie-Grensleie in Gent", "lat": 51.03317, "lon": 3.64483},
                        {"code": "OW168900", "name": "Zeeschelde in Melle", "lat": 51.00578, "lon": 3.80358}]
            sites = nf.vmm_sites(stations, city="Ghent")
            texts = {}
            for st in stations:
                with open(os.path.join(folder, "vmm_%s.tsv" % st["code"]), encoding="utf-8") as fh:
                    texts[st["code"]] = fh.read()
            last = max(l.split("\t")[1] for t in texts.values() for l in t.splitlines()[1:] if l.count("\t") > 2)
            since = (date.fromisoformat(last) - timedelta(days=365)).isoformat()
            records, stats = nf.parse_vmm(texts, {x["site_id"]: x for x in sites}, since=since)
            label, lic = "Real EU data: Ghent rivers (VMM, Flanders)", "VMM, modellicentie gratis hergebruik"
        else:
            folder, city = os.path.join(ROOT, "data", "real-eu-benevento"), "Benevento"
            with open(os.path.join(folder, "stazioni.csv"), encoding="utf-8") as fh:
                sites = nf.arpac_sites(fh.read(), {"C8", "C9", "S7", "S8", "Se", "Sn", "Ta3"}, city="Benevento")
            with open(os.path.join(folder, "results.csv"), encoding="utf-8") as fh:
                records, stats = nf.parse_arpac(fh.read(), {x["site_id"]: x for x in sites})
            last = max(r["observed_at"][:10] for r in records)
            since = (date.fromisoformat(last) - timedelta(days=365)).isoformat()
            stats["older_than_since"] = sum(1 for r in records if r["observed_at"][:10] < since)
            records = [r for r in records if r["observed_at"][:10] >= since]
            stats["records"] = len(records)
            label, lic = "Real EU data: Benevento rivers (ARPA Campania)", "ARPA Campania open data, CC BY"
        used = {r["site_id"] for r in records}
        stats["sites"] = len(used)
        _dump(os.path.join(folder, "sites.json"), {"_note": "REAL public river monitoring stations (%s)." % lic,
                                                    "sites": [x for x in sites if x["site_id"] in used]})
        _dump(os.path.join(folder, "assessments.json"), {
            "_note": "REAL public monitoring data (%s), the 12 months up to the latest sample; see SOURCE.txt." % lic,
            "dataset": {"synthetic": False, "observer_kind": "organisation", "label": label, "import_stats": stats,
                        "source_query": "see SOURCE.txt", "evaluate_each_site_at_its_latest_visit": True},
            "demo_as_of": last + "T23:59:59+01:00", "records": records})
        print(json.dumps(stats, indent=1))
    elif cmd == "eea-coverage":
        from ..adapters.waterbase_importer import coverage
        with open(args.file, encoding="utf-8") as fh:
            data = json.load(fh)
        print(json.dumps({c: dict(coverage(v["rows"]), surface_sites=len(v["surface_sites"])) for c, v in data["cities"].items()}, indent=1))
    elif cmd == "interop-demo":
        import urllib.parse
        agency, citizens, flag = interop_bundles(svc, args.site)
        out = {"agency": svc.share(agency, live=args.live), "citizens": [svc.share(c, live=args.live) for c in citizens],
               "flag": svc.share(flag, live=args.live) if flag else None}
        if args.live:
            base = svc.server.base_url
            loc = [l for l in out["agency"]["locations"] if l.startswith("Location/")][0].split("/_history")[0]

            def get(url):
                status, raw = svc.server.transport("GET", url, None, {"Accept": "application/fhir+json"})
                return json.loads(raw.decode("utf-8"))
            q = "%s/Observation?code=%s&subject=%s" % (base, urllib.parse.quote(
                "http://dd.eionet.europa.eu/vocabulary/wise/ObservedProperty|CAS_14797-55-8", safe=":/"), loc)
            fq = "%s/Flag?subject=%s" % (base, loc)
            obs, flags = get(q), get(fq)
            captured = {
                "_note": "Captured response of the live interop demo on the public HAPI R4 test server (it may purge data; "
                         "re-create with: python3 -m streamfhir --data data/real-eu-toulouse interop-demo --live).",
                "retrieved": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "site_id": args.site,
                "location": loc, "nitrate_query": q, "flag_query": fq,
                "nitrate_results": [{"id": "Observation/" + e["resource"]["id"],
                                     "category": e["resource"]["category"][0]["coding"][0]["code"],
                                     "value_mg_per_l": e["resource"]["valueQuantity"]["value"],
                                     "effective": e["resource"]["effectiveDateTime"],
                                     "performer": e["resource"]["performer"][0].get("display"),
                                     "synthetic": "HTEST" in {x["code"] for x in e["resource"]["meta"].get("security", [])}}
                                    for e in obs.get("entry", [])],
                "flags": [{"id": "Flag/" + e["resource"]["id"], "status": e["resource"]["status"],
                           "code": e["resource"]["code"]["coding"][0]["code"],
                           "period": e["resource"].get("period"),
                           "rules": [x["valueCodeableConcept"]["coding"][0]["code"] for x in e["resource"].get("extension", [])
                                     if "valueCodeableConcept" in x],
                           "synthetic": "HTEST" in {x["code"] for x in e["resource"]["meta"].get("security", [])}}
                          for e in flags.get("entry", [])],
                "raw_nitrate_bundle": obs, "raw_flag_bundle": flags}
            _dump(os.path.join(args.data or os.path.join(ROOT, "data"), "interop-query.json"), captured)
            out["captured"] = {k: v for k, v in captured.items() if not k.startswith("raw_")}
        print(json.dumps(out, indent=1))
    elif cmd == "bench":
        recs = [r for r in svc.records.all() if svc.check_record(r).bundle]
        t = time.perf_counter()
        for i in range(args.n):
            svc.check_record(recs[i % len(recs)])
        dt = time.perf_counter() - t
        print("validated + mapped %d records in %.2f s = %.0f records/s (single thread, this machine)" % (
            args.n, dt, args.n / dt))
