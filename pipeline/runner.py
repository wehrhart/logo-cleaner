"""Row-processing orchestration: discovery -> verification -> logo -> normalize.

Flow per row (cheapest authoritative path first):
  1. HIFLD dataset match  -> identity + open/closed status + maybe website
  2. Try HIFLD website: verify identity on-page, extract + evaluate logo
     candidates. Strong hit -> done (no Wikimedia traffic).
  3. Wikidata/Wikipedia   -> website lead + freely-licensed logo + parent org
  4. Headless-Chromium render fallback for JS-only sites.
  5. Accept best candidate above threshold, else manual_review with evidence.
"""
import json
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from . import config, hifld, imaging, render, sitelogo, systems, util, wiki

_log_lock = threading.Lock()

STRONG_SOURCE_WEIGHT = 0.75  # sources trusted enough for an early accept


def _log_event(event: dict):
    event["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _log_lock:
        with open(config.LOGS_DIR / "events.jsonl", "a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _identity_conf(disc_conf: float, site_conf: float, signals: dict) -> float:
    base = 0.08 + 0.40 * disc_conf + 0.60 * site_conf
    # page confirms the exact location AND carries the facility's name.
    # Location alone is NOT identity: a same-town different-org site (e.g. a
    # veterinary clinic in the same city+zip) matches city/zip/state perfectly.
    if signals.get("city") and (signals.get("zip") or signals.get("state")) \
            and signals.get("name_cov", 0) > 0.15:
        base += 0.15
    # authoritative directory pointed here AND the domain carries the name:
    # trust that even when the page text is thin (JS-rendered sites)
    if signals.get("domain"):
        base = max(base, 0.72 * disc_conf + 0.28 * site_conf)
    elif signals.get("name_cov", 0) == 0:
        base = min(base, 0.55)  # no name evidence anywhere -> never auto-accept
    return min(1.0, round(base, 3))


class RowContext:
    """Mutable per-row processing state."""

    def __init__(self, row):
        self.row = row
        self.attempts = []
        self.evaluated = []
        self.best = None          # (conf, cand, img)
        self.best_site = None     # dict(url, identity_conf, meta, signals)
        self.tried_domains = set()


def _root_of(url: str) -> str:
    from urllib.parse import urlparse
    p = urlparse(url)
    return f"{p.scheme or 'https'}://{p.netloc}/"


def _try_site(ctx: RowContext, url: str, disc_conf: float, origin: str,
              account: str = None, min_relevant: float = 0.15,
              identity_cap: float = None):
    """Fetch+verify a website lead, evaluate its logo candidates.

    Tries the given URL, then falls back to the domain root (deep links from
    directories/Wikidata often go stale while the root stays good). Falls back
    to a headless-Chromium render when plain HTTP is blocked or the page is
    JS-rendered.
    """
    row = ctx.row
    account = account or row["account"]
    dom = util.registrable_domain(url)
    if not dom or dom in ctx.tried_domains or dom in util.AGGREGATOR_DOMAINS:
        return
    ctx.tried_domains.add(dom)

    variants = [url]
    root = _root_of(url)
    if root.rstrip("/") != url.rstrip("/"):
        variants.append(root)

    for u in variants:
        html, final_url, err = sitelogo.fetch_site(u)
        if not html:
            ctx.attempts.append({"step": "fetch_site", "url": u, "error": (err or "")[:250]})
            # WAF/bot-blocked or JS-only origin: a real browser often gets through
            if disc_conf >= 0.55:
                html = render.render(u)
                if html:
                    final_url = u
                    ctx.attempts.append({"step": "fetch_site_render", "url": u, "ok": True})
            if not html:
                continue
        site_conf, detail, signals = sitelogo.verify_site(
            html, final_url, account, row["city"], row["state"], row["zip"])
        identity = _identity_conf(disc_conf, site_conf, signals)
        # redirect vouching: the domain an authoritative directory listed for
        # this facility redirected here — the rebranded site inherits identity
        # (e.g. snhmc.org -> snhhealth.org after a rename), provided the
        # destination still reads as a healthcare organization.
        final_dom = util.registrable_domain(final_url)
        if (final_dom != util.registrable_domain(u)
                and final_dom not in util.AGGREGATOR_DOMAINS
                and not signals.get("parked_page")
                and identity_cap is None):
            vouched = round(max(identity, 0.80 * disc_conf), 3)
            if vouched > identity:
                ctx.attempts.append({"step": "redirect_vouch", "from": u[:120],
                                     "to": final_url[:120], "identity": vouched})
                identity = vouched
        if identity_cap is not None:
            # parent-system leads: verifying the parent's site against the
            # parent's own name is circular, so identity comes from the
            # facility->parent link strength, gated by the site being on-brand
            identity = min(identity_cap if site_conf >= 0.15 else identity_cap * 0.7,
                           identity)
        ctx.attempts.append({"step": "verify_site", "url": final_url, "origin": origin,
                             "site_conf": site_conf, "identity_conf": identity, "detail": detail})
        cands, meta = sitelogo.extract_candidates(html, final_url)
        site = {"url": final_url, "identity_conf": identity, "meta": meta,
                "signals": signals, "origin": origin}
        if not ctx.best_site or identity > ctx.best_site["identity_conf"]:
            ctx.best_site = site
        _evaluate(ctx, cands, identity)

        # JS-rendered page: static HTML had no strong result -> render once
        best_here = max((e.get("conf", 0) for e in ctx.evaluated), default=0)
        if (best_here < config.ACCEPT_THRESHOLD and (identity >= 0.5 or disc_conf >= 0.6)):
            rhtml = render.render(final_url)
            if rhtml and len(rhtml) > 2000:
                r_site_conf, r_detail, r_signals = sitelogo.verify_site(
                    rhtml, final_url, account, row["city"], row["state"], row["zip"])
                merged = {k: (r_signals.get(k) or signals.get(k)) for k in r_signals}
                r_identity = _identity_conf(disc_conf, max(site_conf, r_site_conf), merged)
                ctx.attempts.append({"step": "render_site", "url": final_url,
                                     "site_conf": r_site_conf, "identity_conf": r_identity,
                                     "detail": r_detail})
                rcands, rmeta = sitelogo.extract_candidates(rhtml, final_url)
                if r_identity > (ctx.best_site or {}).get("identity_conf", 0):
                    ctx.best_site = {"url": final_url, "identity_conf": r_identity,
                                     "meta": rmeta, "signals": r_signals,
                                     "origin": origin + "+render"}
                _evaluate(ctx, rcands, max(r_identity, identity))
                site_conf = max(site_conf, r_site_conf)
        if site_conf >= min_relevant:
            return  # page is clearly about the facility; no root retry needed


def _evaluate(ctx: RowContext, cands, identity_conf: float):
    """Download/score candidates; keep the best. Stops early on a great hit."""
    seen = {e["url"] for e in ctx.evaluated}
    banned = ctx.row.get("_banned") or set()
    for cand in cands[:8]:
        if cand["url"][:300] in seen or cand["url"][:300] in banned:
            continue
        entry = {"url": cand["url"][:300], "source": cand["source"],
                 "weight": cand["weight"], "identity_conf": identity_conf}
        data, ct, err = imaging.fetch_image(cand["url"])
        if err:
            entry["error"] = err[:200]
            ctx.evaluated.append(entry)
            continue
        img, kind, err = imaging.load_image(data, ct, cand["url"])
        if err:
            entry["error"] = err[:200]
            ctx.evaluated.append(entry)
            continue
        img = imaging.trim(img)
        m = imaging.analyze(img, kind)
        qf = imaging.quality_factor(m, cand["source"])
        conf = round(cand["weight"] * identity_conf * (0.55 + 0.45 * qf), 3) if qf > 0 else 0.0
        entry.update({"kind": kind, "metrics": m, "quality": qf, "conf": conf})
        ctx.evaluated.append(entry)
        if qf <= 0:
            continue
        if not ctx.best or conf > ctx.best[0]:
            ctx.best = (conf, {**cand, "identity_conf": identity_conf}, img)
        if conf >= max(config.ACCEPT_THRESHOLD, 0.85):
            return


def process_row(row: dict) -> dict:
    ctx = RowContext(row)
    rid = row["id"]

    # ---- 1. HIFLD identity ------------------------------------------------
    hifld_rec, hifld_score, hifld_note = hifld.match(
        row["account"], row["address"], row["city"], row["state"], row["zip"])
    ctx.attempts.append({"step": "hifld_match", "score": hifld_score, "note": hifld_note})
    facility_closed = bool(hifld_rec and hifld_score >= 0.62
                           and (hifld_rec.get("STATUS") or "").upper() == "CLOSED")

    # name variants: renamed facilities verify against their CURRENT name
    name_variants = [row["account"]]
    if hifld_rec and hifld_score >= config.HIFLD_MATCH_THRESHOLD:
        for k in ("NAME", "ALT_NAME"):
            v = (hifld_rec.get(k) or "").strip()
            if v and v.upper() != "NOT AVAILABLE" and v not in name_variants:
                name_variants.append(v)

    # ---- 2. HIFLD website first (cheap, authoritative) ----------------------
    if hifld_rec and hifld_score >= config.HIFLD_MATCH_THRESHOLD and not facility_closed:
        w = hifld.website_of(hifld_rec)
        if w:
            _try_site(ctx, w, hifld_score, "hifld", account=name_variants)

    strong_hit = ctx.best and ctx.best[0] >= config.ACCEPT_THRESHOLD \
        and ctx.best[1]["weight"] >= STRONG_SOURCE_WEIGHT

    # ---- 3. Wikidata/Wikipedia for everything the fast path didn't nail ----
    wd = None
    if not strong_hit:
        lat = lon = None
        if hifld_rec and hifld_score >= config.HIFLD_MATCH_THRESHOLD:
            lat, lon = hifld_rec.get("LATITUDE"), hifld_rec.get("LONGITUDE")
        try:
            wd = wiki.match_local(row["account"], row["city"], row["state"], lat, lon)
            # renamed facility: the CURRENT name may be the one Wikidata knows.
            # Higher bar than the primary lookup: a partial-token match against
            # the wrong same-city entity is worse than no match.
            if not wd and len(name_variants) > 1:
                wd = wiki.match_local(name_variants[1], row["city"], row["state"],
                                      lat, lon, min_score=0.75)
            if not wd:
                wd = wiki.lookup_tail(row["account"], row["city"], row["state"])
        except Exception as e:
            ctx.attempts.append({"step": "wikidata", "error": repr(e)[:200]})
        if wd:
            ctx.attempts.append({"step": "wikidata", "qid": wd["qid"], "label": wd["label"],
                                 "score": wd["score"], "website": wd.get("website", ""),
                                 "logo": wd.get("logo_file", ""), "parent": wd.get("parent_system", "")})
            if wd.get("label") and wd["label"] not in name_variants:
                name_variants.append(wd["label"])
            if wd.get("website") and not facility_closed:
                _try_site(ctx, wd["website"], wd["score"], "wikidata",
                          account=name_variants)
            if wd.get("logo_url"):
                _evaluate(ctx, [{"url": wd["logo_url"], "source": "wikidata_logo",
                                 "weight": sitelogo.W_WIKIDATA_LOGO}], wd["score"])

    # ---- 3b. parent-health-system branding fallback -------------------------
    # When the facility has no working site of its own, its current parent
    # system's branding is acceptable (and often the only correct answer after
    # an acquisition). Parent evidence: Wikidata P749/P127, or the HIFLD
    # ALT_NAME carrying the corporate/system name.
    best_conf = ctx.best[0] if ctx.best else 0.0
    if best_conf < config.ACCEPT_THRESHOLD and not facility_closed:
        parent_leads = []
        for p in ((wd or {}).get("parent_system") or "").split(";"):
            p = p.strip()
            if p:
                parent_leads.append((p, (wd["score"] if wd else 0.6) * 0.92, "wikidata_parent"))
        if hifld_rec and hifld_score >= config.HIFLD_MATCH_THRESHOLD:
            alt = (hifld_rec.get("ALT_NAME") or "").strip()
            if alt and alt.upper() != "NOT AVAILABLE" \
                    and util.name_similarity(alt, row["account"]) < 0.85:
                parent_leads.append((alt, hifld_score * 0.85, "hifld_alt_name"))
            # renamed facilities carry the system name as a prefix of their
            # CURRENT name: "ASCENSION PROVIDENCE ROCHESTER HOSPITAL" -> Ascension
            cur = (hifld_rec.get("NAME") or "").strip()
            if cur and util.name_similarity(cur, row["account"]) < 0.75:
                toks = cur.split()
                for k in (3, 2, 1):
                    if len(toks) > k:
                        parent_leads.append((" ".join(toks[:k]), hifld_score * 0.95,
                                             "hifld_current_name_prefix"))
        for pname, link_conf, porigin in parent_leads[:5]:
            org, sim = wiki.match_org(pname, facility_state=row["state"])
            if not org or not org.get("website"):
                cur = systems.lookup(pname)
                if cur:
                    org, sim = {"label": cur[0], "website": cur[1]}, 1.0
                    porigin += "+registry"
            if not org or not org.get("website"):
                continue
            ctx.attempts.append({"step": "parent_org", "query": pname[:80],
                                 "matched": org["label"], "sim": round(sim, 3),
                                 "website": org["website"], "origin": porigin})
            link = min(link_conf, 0.95) * sim
            _try_site(ctx, org["website"], link, porigin,
                      account=org["label"], identity_cap=link)
            if org.get("logo_url"):
                _evaluate(ctx, [{"url": org["logo_url"], "source": "wikidata_logo",
                                 "weight": sitelogo.W_WIKIDATA_LOGO}],
                          min(link_conf, 0.95) * sim)
            if ctx.best and ctx.best[0] >= config.ACCEPT_THRESHOLD:
                break

    # ---- 4. metadata -------------------------------------------------------
    parent = (wd or {}).get("parent_system") or ""
    if not parent and ctx.best_site:
        mn = ctx.best_site["meta"].get("org_name") or ctx.best_site["meta"].get("og_site_name") or ""
        if mn and util.name_similarity(mn, row["account"]) < 0.75:
            parent = mn[:200]

    common = {
        "website": ctx.best_site["url"] if ctx.best_site else "",
        "parent_system": parent,
        "attempts_json": ctx.attempts,
        "candidates_json": ctx.evaluated,
        "processed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "error": None,
    }

    # ---- 5. decide -----------------------------------------------------------
    if ctx.best and ctx.best[0] >= config.ACCEPT_THRESHOLD and not facility_closed:
        conf, cand, img = ctx.best
        out = imaging.normalize_to_square(img)
        fname = config.filename_for(rid)
        imaging.save_png(out, config.LOGOS_DIR / fname)
        problems = imaging.validate_file(config.LOGOS_DIR / fname)
        if not problems:
            src_dom = (util.registrable_domain(cand["url"])
                       if not cand["url"].startswith("data:")
                       else util.registrable_domain(common["website"]))
            return {**common, "status": "completed", "confidence": conf,
                    "filename": fname, "source_url": cand["url"][:500],
                    "source_domain": src_dom,
                    "verification_notes": _notes(hifld_note, wd, ctx.best_site, cand),
                    "review_reason": None}
        (config.LOGOS_DIR / fname).unlink(missing_ok=True)
        return {**common, "status": "manual_review", "confidence": conf,
                "review_reason": f"output failed validation: {problems}",
                "verification_notes": _notes(hifld_note, wd, ctx.best_site, cand)}

    if facility_closed:
        reason = "facility marked CLOSED in HIFLD dataset; branding continuity unclear"
    elif not ctx.tried_domains and not (wd and wd.get("logo_url")):
        reason = "no authoritative website or logo source found"
    elif not ctx.best_site and not (wd and wd.get("logo_url")):
        reason = "official website unreachable or unverifiable"
    elif ctx.best:
        reason = f"best candidate confidence {ctx.best[0]:.2f} below threshold {config.ACCEPT_THRESHOLD}"
    else:
        reason = "no usable logo candidate on verified sources"
    return {**common, "status": "manual_review",
            "confidence": ctx.best[0] if ctx.best else 0.0,
            "review_reason": reason,
            "verification_notes": _notes(hifld_note, wd, ctx.best_site,
                                         ctx.best[1] if ctx.best else None)}


def _notes(hifld_note, wd, best_site, cand):
    parts = [hifld_note or ""]
    if wd:
        parts.append(f"Wikidata {wd['qid']} '{wd['label']}' score={wd['score']}")
    if best_site:
        parts.append(f"site={best_site['url']} identity_conf={best_site['identity_conf']:.2f} via {best_site['origin']}")
    if cand:
        parts.append(f"logo source={cand['source']}")
    return " | ".join(p for p in parts if p)


def run(state, limit=None, include_failed=False, workers=None, progress_every=25):
    """Process all pending rows with a thread pool; checkpoint each row."""
    ids = state.pending_ids(limit=limit, include_failed=include_failed)
    if not ids:
        print("nothing to process")
        return
    workers = workers or config.CONCURRENCY
    start = time.time()
    done = 0
    print(f"processing {len(ids)} rows with {workers} workers", flush=True)

    def work(rid):
        row = state.get_row(rid)
        row["_banned"] = state.banned_urls(rid)
        try:
            return rid, process_row(row), None
        except Exception:
            return rid, None, traceback.format_exc()[-1500:]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for rid in ids:
            state.mark_processing(rid)
            futures[pool.submit(work, rid)] = rid
        for fut in as_completed(futures):
            rid, result, err = fut.result()
            if err:
                state.update_row(rid, status="failed", error=err,
                                 processed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
                _log_event({"row": rid, "status": "failed", "error": err[:300]})
            else:
                state.update_row(rid, **result)
                _log_event({"row": rid, "status": result["status"],
                            "conf": result.get("confidence"),
                            "src": result.get("source_url", "")[:120]})
            done += 1
            if done % progress_every == 0 or done == len(ids):
                c = state.counts()
                rate = done / max(time.time() - start, 1) * 3600
                remaining = c["pending"] + c["processing"]
                eta_h = remaining / rate if rate else 0
                print(f"[{done}/{len(ids)}] completed={c['completed']} review={c['manual_review']} "
                      f"failed={c['failed']} remaining={remaining} rate={rate:.0f}/h eta={eta_h:.1f}h",
                      flush=True)
                state.set_meta("last_progress", {**c, "rate_per_hour": round(rate)})
