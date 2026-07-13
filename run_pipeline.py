#!/usr/bin/env python3
"""CLI for the hospital logo pipeline.

Usage:
  python run_pipeline.py ingest              # load workbook into state DB
  python run_pipeline.py fetch-hifld         # download the HIFLD hospital dataset
  python run_pipeline.py run [--limit N] [--ids 8,10] [--include-failed] [--workers N]
  python run_pipeline.py status
  python run_pipeline.py qa                  # validation + manifest + review + summary
  python run_pipeline.py package             # qa artifacts + ZIP of accepted logos
  python run_pipeline.py reset --ids 8,10    # send rows back to pending
"""
import argparse
import json
import sys

from pipeline import config, hifld, ingest, qa, runner
from pipeline.state import State


def cmd_ingest(args):
    rows, problems = ingest.read_workbook()
    st = State()
    st.seed_rows(rows)
    c = st.counts()
    print(f"workbook rows: {len(rows)}; ingest problems: {len(problems)}")
    for p in problems[:20]:
        print("  !", p)
    print("state:", c)


def cmd_fetch_hifld(args):
    n = hifld.download(force=args.force)
    print(f"HIFLD hospital records cached: {n}")


def cmd_fetch_wikidata(args):
    from pipeline import wiki
    n = wiki.download(force=args.force)
    print(f"Wikidata hospital entities cached: {n}")


def cmd_run(args):
    st = State()
    reset = st.reset_stuck_processing()
    if reset:
        print(f"reset {reset} stuck 'processing' rows to pending")
    if args.ids:
        for rid in [int(x) for x in args.ids.split(",")]:
            st.update_row(rid, status="pending")
    runner.run(st, limit=args.limit, include_failed=args.include_failed,
               workers=args.workers)
    print("final:", st.counts())


def cmd_status(args):
    st = State()
    c = st.counts()
    print(json.dumps({**c, "last_progress": st.get_meta("last_progress")}, indent=2))


def cmd_qa(args):
    st = State()
    result = qa.validate(st)
    m = qa.write_manifest(st)
    r = qa.write_review_file(st)
    s = qa.write_summary(st, result)
    print(f"manifest: {m}\nreview:   {r}\nsummary:  {s}")
    print(f"QA problems: {len(result['problems'])}")
    for p in result["problems"][:30]:
        print("  !", p)


def cmd_package(args):
    st = State()
    result = qa.validate(st)
    qa.write_manifest(st)
    qa.write_review_file(st)
    qa.write_summary(st, result)
    z = qa.make_zip(st)
    print(f"zip: {z}")
    print(f"QA problems: {len(result['problems'])}")


def cmd_reset(args):
    st = State()
    ids = [int(x) for x in args.ids.split(",")]
    for rid in ids:
        st.update_row(rid, status="pending", filename=None, source_url=None,
                      confidence=None, review_reason=None, error=None)
    print(f"reset {len(ids)} rows to pending")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest").set_defaults(fn=cmd_ingest)
    p = sub.add_parser("fetch-hifld")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_fetch_hifld)
    p = sub.add_parser("fetch-wikidata")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_fetch_wikidata)
    p = sub.add_parser("run")
    p.add_argument("--limit", type=int)
    p.add_argument("--ids")
    p.add_argument("--workers", type=int)
    p.add_argument("--include-failed", action="store_true")
    p.set_defaults(fn=cmd_run)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("qa").set_defaults(fn=cmd_qa)
    sub.add_parser("package").set_defaults(fn=cmd_package)
    p = sub.add_parser("reset")
    p.add_argument("--ids", required=True)
    p.set_defaults(fn=cmd_reset)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
