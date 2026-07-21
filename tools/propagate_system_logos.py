#!/usr/bin/env python3
"""Lever 1: propagate verified system logos to sibling facilities.

Many review rows are facilities inside national health systems that use ONE
unified brand across every site (HCA Florida, AdventHealth, CommonSpirit, ...).
For those systems we already have a verified logo from a completed sibling, so
we can safely assign the identical image to the still-unresolved siblings.

Safety rules:
  * Only systems on UNIFIED_BRANDS (curated: one logo for all facilities).
  * A row's *current owner* is read from its HIFLD record (NAME/ALT_NAME), the
    authoritative "who runs it today" signal — not the legacy workbook name.
  * The canonical logo is the most common shared image among completed siblings
    from that system's domain (verified during the main run + visual QA).
  * Closed facilities are skipped.

Usage: python tools/propagate_system_logos.py [--apply]
"""
import argparse
import hashlib
import json
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import config, hifld, util
from pipeline.state import State

# Domains that are a SINGLE unified brand (one logo for every facility). A review
# row confidently verified to one of these — even if its own logo extraction
# failed — can safely reuse the sibling logo. Shared asset CDNs (hcadam.com,
# mktgcdn.com, cloudinary.com, sitecorecloud.io, *cdn*, gstatic) are EXCLUDED
# because one domain there serves many different regional brand logos.
SAFE_DOMAINS = {
    "hcafloridahealthcare.com", "adventhealth.com", "intermountainhealthcare.org",
    "commonspirit.org", "christushealth.org", "upmc.com", "shrinerschildrens.org",
    "mountainstar.com", "nortonhealthcare.com", "corewellhealth.org",
    "allinahealth.org", "peacehealth.org", "houstonmethodist.org",
    "kindredhospitals.com", "tennova.com", "phs.org", "covenanthealth.com",
    "ardenthealthservices.com", "fmolhs.org", "lcmchealth.org", "brownhealth.org",
    "mercy.net", "ssmhealth.com", "bswhealth.com", "novanthealth.org",
    "atriumhealth.org", "sentara.com", "inova.org", "ochsner.org",
    "memorialhermann.org", "prismahealth.org", "wellstar.org", "bannerhealth.com",
    "sutterhealth.org", "providence.org", "trinity-health.org", "aspirus.org",
    "sanfordhealth.org", "avera.org", "geisinger.org", "wellspan.org",
    "pennmedicine.org", "unitypoint.org", "mclaren.org", "munsonhealthcare.org",
    "beaumont.org", "bjc.org", "osfhealthcare.org", "multicare.org",
    "legacyhealth.org", "scripps.org", "sharp.com", "memorialcare.org",
    "baycare.org", "orlandohealth.com", "adventisthealth.org", "mainlinehealth.org",
    "hackensackmeridianhealth.org", "rwjbh.org",
}
_CDN_HINTS = ("cdn", "cloudinary", "sitecore", "gstatic", "squarespace",
              "mktgcdn", "hcadam", "azureedge", "cloudfront", "amazonaws")

# brand token that must appear in the facility's HIFLD current name  ->  the
# system domain whose verified logo we reuse. Only unified single-brand systems.
# Signal-A tokens must be GLOBALLY UNIQUE brand names (one system nationwide).
# Ambiguous tokens are excluded because several unrelated systems share them:
#   PRESBYTERIAN (NM Presbyterian vs Texas Health Presbyterian vs Hollywood
#   vs NY-Presbyterian), COVENANT (TN vs Lubbock vs Swedish Covenant Chicago),
#   NORTON (Norton Healthcare KY vs Norton Community VA/Ballad vs Norton Sound
#   AK), MEMORIAL/MERCY/METHODIST/BAPTIST (dozens of unrelated systems).
UNIFIED_BRANDS = {
    "HCA FLORIDA": "hcafloridahealthcare.com",
    "ADVENTHEALTH": "adventhealth.com",
    "INTERMOUNTAIN": "intermountainhealthcare.org",
    "COMMONSPIRIT": "commonspirit.org",
    "CHRISTUS": "christushealth.org",
    "UPMC": "upmc.com",
    "SHRINERS": "shrinerschildrens.org",
    "BAYLOR SCOTT": "baylorfrisco.com",
    "MOUNTAINSTAR": "mountainstar.com",
    "COREWELL": "corewellhealth.org",
    "ALLINA": "allinahealth.org",
    "PEACEHEALTH": "peacehealth.org",
    "HOUSTON METHODIST": "houstonmethodist.org",
    "KINDRED HEALTHCARE": "kindredhospitals.com",
    "TENNOVA": "tennova.com",
    "MEMORIAL HERMANN": "memorialhermann.org",
    "PRISMA HEALTH": "prismahealth.org",
}


def fhash(fn):
    p = config.LOGOS_DIR / fn
    return hashlib.md5(p.read_bytes()).hexdigest() if p.exists() else None


def canonical_logos(st):
    """domain -> (filename, n_siblings) for the dominant shared image."""
    comp = [r for r in st.all_rows() if r["status"] == "completed" and r["filename"]]
    by_dom = defaultdict(list)
    for r in comp:
        if r["source_domain"]:
            by_dom[r["source_domain"]].append(r)
    out = {}
    for dom, members in by_dom.items():
        hashes = Counter(fhash(m["filename"]) for m in members)
        top_hash, n = hashes.most_common(1)[0]
        if not top_hash or n < 2:
            continue
        fn = next(m["filename"] for m in members if fhash(m["filename"]) == top_hash)
        out[dom] = (fn, n)
    return out


def _row_domains(r):
    """System domains this review row already touched: its website, and any
    site it verified with reasonable on-page confidence during the run."""
    doms = set()
    if r.get("website"):
        doms.add(util.registrable_domain(r["website"]))
    for a in json.loads(r.get("attempts_json") or "[]"):
        if a.get("step") in ("verify_site", "render_site") and a.get("site_conf", 0) >= 0.3:
            d = util.registrable_domain(a.get("url", ""))
            if d:
                doms.add(d)
    return {d for d in doms if d and not any(h in d for h in _CDN_HINTS)}


def run(apply=False):
    st = State()
    canon = canonical_logos(st)
    review = [r for r in st.all_rows() if r["status"] == "manual_review"]
    plan = defaultdict(list)   # domain -> [(row, rec_name, why), ...]
    for r in review:
        rec, score, note = hifld.match(r["account"], r["address"], r["city"], r["state"], r["zip"])
        if rec and (rec.get("STATUS") or "").upper() == "CLOSED":
            continue
        owner = (rec.get("NAME") if rec and score >= 0.62 else None)

        matched = None
        # signal A: HIFLD current name carries a unified brand token
        if rec and score >= 0.62:
            cur = (rec.get("NAME") or "").upper() + " | " + (rec.get("ALT_NAME") or "").upper()
            for brand, dom in UNIFIED_BRANDS.items():
                if brand in cur and dom in canon:
                    matched = (dom, "hifld-name")
                    break
        # signal B (verified-domain) removed: a review row's prior verification
        # can itself be the wrong match that landed it in review, so reusing it
        # propagates that error (e.g. St Joseph WV -> Intermountain). Only the
        # authoritative HIFLD current-name brand (signal A) is trusted.
        if matched:
            plan[matched[0]].append((r, owner, matched[1]))

    total = sum(len(v) for v in plan.values())
    print(f"{'APPLYING' if apply else 'DRY RUN'} — sibling-logo propagation")
    for dom, items in sorted(plan.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(items):>4}  <- {dom}  (logo {canon[dom][0]})")
    print(f"TOTAL recoverable: {total}")

    if not apply:
        return total
    n = 0
    for dom, items in plan.items():
        src_fn, _ = canon[dom]
        src = config.LOGOS_DIR / src_fn
        for r, owner, why in items:
            dst_fn = config.filename_for(r["id"])
            shutil.copyfile(src, config.LOGOS_DIR / dst_fn)
            st.update_row(
                r["id"], status="completed", filename=dst_fn,
                source_url=f"system-brand:{dom}", source_domain=dom,
                parent_system=owner or dom, confidence=0.75,
                review_reason=None, error=None,
                verification_notes=(f"Sister-hospital propagation ({why}): facility is part "
                                    f"of unified-brand system {dom}; logo shared from a "
                                    f"verified sibling."),
            )
            n += 1
    print(f"applied: {n} rows now completed via system-brand propagation")
    return n


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    run(**vars(ap.parse_args()))
