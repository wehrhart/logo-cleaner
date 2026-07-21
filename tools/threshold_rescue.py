#!/usr/bin/env python3
"""Lever 2: rescue below-threshold candidates for human visual QA.

~27% of the review queue already found a logo — often the right one — that
scored just under the 0.70 auto-accept bar. This rebuilds those logos (from the
cached candidate the main run already fetched) into a PROVISIONAL completed
state so they can be visually confirmed in contact sheets. Anything that fails
the eyeball test is demoted+banned afterward.

Only genuine logo sources are rescued (JSON-LD / header / img logos / inline
SVG / manifest & touch icons / Wikidata) — never a favicon-only fallback, a
photo, or a rejected/blank image. Banned URLs are honored.

Usage: python tools/threshold_rescue.py [--apply] [--min 0.45] [--max 0.70]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import config, imaging
from pipeline.state import State

GOOD_SOURCES = {
    "jsonld_logo", "header_img_logo", "img_logo", "inline_svg",
    "manifest_icon", "apple_touch_icon", "ms_tile", "wikidata_logo", "icon_link",
}


def best_candidate(row, lo, hi):
    banned = set(json.loads(row.get("candidates_json") and "[]" or "[]"))
    st = State()
    banned = st.banned_urls(row["id"])
    best = None
    for c in json.loads(row.get("candidates_json") or "[]"):
        if c.get("source") not in GOOD_SOURCES:
            continue
        conf = c.get("conf", 0) or 0
        if conf < lo or conf > hi:
            continue
        m = c.get("metrics") or {}
        if m.get("reject") or m.get("photo_like") or m.get("white_on_transparent"):
            continue
        if c.get("url") in banned:
            continue
        if not best or conf > best.get("conf", 0):
            best = c
    return best


def run(apply=False, lo=0.45, hi=0.70):
    st = State()
    review = [r for r in st.all_rows() if r["status"] == "manual_review"]
    picks = []
    for r in review:
        c = best_candidate(r, lo, hi)
        if c:
            picks.append((r, c))
    print(f"{'APPLYING' if apply else 'DRY RUN'} threshold-rescue [{lo}, {hi}] — {len(picks)} candidates")
    if not apply:
        from collections import Counter
        src = Counter(c["source"] for _, c in picks)
        for s, n in src.most_common():
            print(f"  {n:>4}  {s}")
        return len(picks)

    ok = 0
    for r, c in picks:
        data, ct, err = imaging.fetch_image(c["url"])
        if err:
            continue
        img, kind, err = imaging.load_image(data, ct, c["url"])
        if err:
            continue
        img = imaging.trim(img)
        m = imaging.analyze(img, kind)
        if m.get("reject") or m.get("white_on_transparent"):
            continue
        out = imaging.normalize_to_square(img)
        fn = config.filename_for(r["id"])
        imaging.save_png(out, config.LOGOS_DIR / fn)
        if imaging.validate_file(config.LOGOS_DIR / fn):
            (config.LOGOS_DIR / fn).unlink(missing_ok=True)
            continue
        st.update_row(r["id"], status="completed", filename=fn,
                      source_url=c["url"], source_domain=r["source_domain"],
                      confidence=c.get("conf"),
                      review_reason=None, error=None,
                      verification_notes=(r["verification_notes"] or "")
                      + " | threshold-rescue provisional (visual-QA required)")
        ok += 1
    print(f"rescued to provisional-completed: {ok}")
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--min", type=float, default=0.45)
    ap.add_argument("--max", type=float, default=0.70)
    a = ap.parse_args()
    run(apply=a.apply, lo=a.min, hi=a.max)
