"""HIFLD/ORNL national hospital dataset: identity verification + official websites."""
import json
import threading

from . import config, net, util

_lock = threading.Lock()
_index = None


def download(force: bool = False) -> int:
    """Page through the ArcGIS feature service and cache all hospital records."""
    if config.HIFLD_CACHE.exists() and not force:
        return len(json.loads(config.HIFLD_CACHE.read_text()))
    records, offset = [], 0
    while True:
        data, res = net.get_json(config.HIFLD_QUERY_URL, params={
            "where": "1=1", "outFields": "*", "f": "json",
            "resultOffset": offset, "resultRecordCount": 1000,
            "orderByFields": "FID",
        }, cache=False)
        if not data or "features" not in data:
            raise RuntimeError(f"HIFLD download failed at offset {offset}: {res.error or res.status}")
        feats = data["features"]
        if not feats:
            break
        records.extend(f["attributes"] for f in feats)
        offset += len(feats)
        if not data.get("exceededTransferLimit") and len(feats) < 1000:
            break
    config.HIFLD_CACHE.write_text(json.dumps(records))
    return len(records)


def _build_index():
    global _index
    with _lock:
        if _index is not None:
            return _index
        records = json.loads(config.HIFLD_CACHE.read_text())
        by_state = {}
        for rec in records:
            st = (rec.get("STATE") or "").upper()
            rec["_zip5"] = util.zip5(rec.get("ZIP"))
            rec["_city"] = util.norm_city(rec.get("CITY"))
            by_state.setdefault(st, []).append(rec)
        _index = by_state
        return _index


def match(account: str, address: str, city: str, state: str, zip_code: str):
    """Find the best HIFLD record for a workbook row.

    Returns (record, score, notes) or (None, 0.0, notes).
    """
    idx = _build_index()
    st = (state or "").upper().strip()
    cands = idx.get(st, [])
    z5 = util.zip5(zip_code)
    ncity = util.norm_city(city)
    snum = util.street_number(address)

    best, best_score = None, 0.0
    for rec in cands:
        name_s = util.name_similarity(account, rec.get("NAME") or "")
        alt = rec.get("ALT_NAME") or ""
        if alt and alt != "NOT AVAILABLE":
            name_s = max(name_s, util.name_similarity(account, alt))
        addr_fingerprint = bool(
            z5 and rec["_zip5"] == z5
            and snum and util.street_number(rec.get("ADDRESS") or "") == snum)
        if name_s < 0.30 and not addr_fingerprint:
            continue
        score = 0.52 * name_s
        if z5 and rec["_zip5"] == z5:
            score += 0.22
        if ncity and rec["_city"] == ncity:
            score += 0.14
        if snum and util.street_number(rec.get("ADDRESS") or "") == snum:
            score += 0.12
        # exact street number + zip identifies the facility even after a
        # rename/acquisition changed everything about its name
        if addr_fingerprint and name_s >= 0.2:
            score = max(score, 0.78)
        if score > best_score:
            best, best_score = rec, score

    if not best:
        return None, 0.0, "no HIFLD candidate in state"
    notes = (f"HIFLD: {best.get('NAME')} @ {best.get('ADDRESS')}, {best.get('CITY')} "
             f"{best.get('STATE')} {best.get('_zip5')} status={best.get('STATUS')}")
    return best, round(min(best_score, 1.0), 3), notes


def website_of(rec) -> str:
    w = (rec or {}).get("WEBSITE") or ""
    w = w.strip()
    if not w or w.upper() in ("NOT AVAILABLE", "NA", "NONE", "N/A"):
        return ""
    if not w.lower().startswith("http"):
        w = "https://" + w
    if util.registrable_domain(w) in util.AGGREGATOR_DOMAINS:
        return ""
    return w
