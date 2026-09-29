"""Command line entry point: python3 -m streamfhir <command>."""
import argparse
import json
import os
import sys

from ..adapters.fhir_mapper import (SID_SITE, codesystem_resources, conformance_bundle, structuredefinition_resource,
                                    valueset_resource)
from ..adapters.presenter import check_json
from ..domain.risk import rules_table
from .container import ROOT, build_service
from .web import make_server


def _record(service, record_id):
    for r in service.records.all():
        if r["record_id"] == record_id:
            return r
    sys.exit("No sample record %s" % record_id)


def main(argv=None):
    p = argparse.ArgumentParser(prog="streamfhir", description="Citizen stream checks -> HL7 FHIR R4 + One Health risk flags")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("serve", help="run the web UI (default)")
    s.add_argument("--port", type=int, default=8000)
    sub.add_parser("overview", help="print risk level per site")
    sub.add_parser("rules", help="print the risk rules")
    c = sub.add_parser("check", help="validate + map one sample record")
    c.add_argument("record_id")
    e = sub.add_parser("export", help="write all Bundles to a folder")
    e.add_argument("--out", default=os.path.join(ROOT, "out"))
    sd = sub.add_parser("send", help="send one record's Bundle (dry run unless --live)")
    sd.add_argument("record_id")
    sd.add_argument("--live", action="store_true", help="really POST to the FHIR server (synthetic data only)")
    sd.add_argument("--flags", action="store_true", help="also send Flag bundles for high-risk sites")
    v = sub.add_parser("validate-remote", help="ask the FHIR server to $validate one generated Observation")
    v.add_argument("record_id")
    v.add_argument("--location-id", help="server id of an existing Location to reference (e.g. from a live send)")
    v.add_argument("--with-profile", action="store_true", help="keep meta.profile (needs publish-conformance first)")
    pc = sub.add_parser("publish-conformance", help="PUT CodeSystems, ValueSet and profile to the FHIR server (dry run unless --live)")
    pc.add_argument("--live", action="store_true")
    sub.add_parser("build-fhir", help="regenerate fhir/ conformance resources")
    args = p.parse_args(argv)
    svc = build_service()
    cmd = args.cmd or "serve"

    if cmd == "serve":
        port = getattr(args, "port", 8000)
        httpd = make_server(svc, "127.0.0.1", port)
        print("StreamFHIR running at http://127.0.0.1:%d  (Ctrl+C to stop)" % port)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    elif cmd == "overview":
        for o in svc.site_overview():
            r = o.risk
            print("%-11s %-9s %2d pts  %s  rules=%s  flag=%s" % (o.site.site_id, r.level.upper(), r.total_points,
                  r.lane_points, [f.rule_id + ("" if f.corroborated else "?") for f in r.fired], bool(o.flag_bundle)))
    elif cmd == "rules":
        for r in rules_table():
            print("%s %-45s %s\n    when: %s\n    why:  %s" % (r["id"], r["title"], r["points"], r["when"], r["why"]))
    elif cmd == "check":
        print(json.dumps(check_json(svc.check_record(_record(svc, args.record_id))), indent=1, ensure_ascii=False))
    elif cmd == "export":
        os.makedirs(args.out, exist_ok=True)
        n = 0
        for r in svc.records.all():
            b = svc.check_record(r).bundle
            if b:
                with open(os.path.join(args.out, "bundle-%s.json" % r["record_id"]), "w") as fh:
                    json.dump(b, fh, indent=1, ensure_ascii=False)
                n += 1
        for o in svc.site_overview():
            if o.flag_bundle:
                with open(os.path.join(args.out, "flag-%s.json" % o.site.site_id), "w") as fh:
                    json.dump(o.flag_bundle, fh, indent=1, ensure_ascii=False)
                n += 1
        print("wrote %d bundles to %s" % (n, args.out))
    elif cmd == "send":
        res = svc.check_record(_record(svc, args.record_id))
        if res.bundle is None:
            sys.exit("Record %s is blocked: %s" % (args.record_id, [i.message for i in res.report.issues]))
        print(json.dumps(svc.share(res.bundle, live=args.live), indent=1))
        if args.flags:
            for o in svc.site_overview():
                if o.flag_bundle:
                    print(o.site.site_id, json.dumps(svc.share(o.flag_bundle, live=args.live), indent=1))
    elif cmd == "validate-remote":
        res = svc.check_record(_record(svc, args.record_id))
        obs = [e["resource"] for e in res.bundle["entry"] if e["resource"]["resourceType"] == "Observation"][1]
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
        items = [("CodeSystem-%s.json" % cs["id"], cs) for cs in codesystem_resources()]
        items += [("ValueSet-stream-indicator.json", valueset_resource()),
                  ("StructureDefinition-stream-assessment-observation.json", structuredefinition_resource())]
        for name, res in items:
            with open(os.path.join(out, name), "w") as fh:
                json.dump(res, fh, indent=1, ensure_ascii=False)
                fh.write("\n")
        print("wrote %d files to %s" % (len(items), out))
