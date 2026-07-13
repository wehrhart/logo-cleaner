"""Wikidata knowledge for US hospitals, bulk-downloaded once via SPARQL.

Per-row Wikidata API calls get rate-limited (429) quickly, so instead we pull
every US hospital-ish entity (label, description, website, logo, coordinates,
admin area, parent org) in one query and match locally. enwiki full-text
search (generously rate-limited) remains as a per-row fallback for rows whose
account name is too abbreviated for direct label matching.
"""
import json
import math
import re
import threading
from urllib.parse import quote

from . import config, net, util

SPARQL_URL = "https://query.wikidata.org/sparql"
WP_API = "https://en.wikipedia.org/w/api.php"
WD_CACHE = config.CACHE_DIR / "wikidata_hospitals.json"

_HOSPITAL_WORDS = re.compile(
    r"hospital|medical cent|health (system|network|care)|clinic|trauma cent", re.I)

# hospital, university hospital, children's hospital, psychiatric hospital,
# teaching hospital, veterans affairs hospital, health system
_CLASSES = "wd:Q16917 wd:Q1059324 wd:Q1774898 wd:Q210999 wd:Q1365207 wd:Q4287745 wd:Q615150"

_QUERY = f"""
SELECT ?item ?itemLabel ?itemDescription ?website ?logo ?coord ?adminLabel ?parentLabel WHERE {{
  VALUES ?cls {{ {_CLASSES} }}
  ?item wdt:P31 ?cls .
  ?item wdt:P17 wd:Q30 .
  OPTIONAL {{ ?item wdt:P856 ?website }}
  OPTIONAL {{ ?item wdt:P154 ?logo }}
  OPTIONAL {{ ?item wdt:P625 ?coord }}
  OPTIONAL {{ ?item wdt:P131 ?admin }}
  OPTIONAL {{ ?item wdt:P749 ?parent }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" . }}
}}
"""

_lock = threading.Lock()
_entities = None


def download(force: bool = False) -> int:
    if WD_CACHE.exists() and not force:
        return len(json.loads(WD_CACHE.read_text()))
    import requests
    r = requests.post(SPARQL_URL, data={"query": _QUERY, "format": "json"},
                      headers={"User-Agent": net._WIKIMEDIA_UA}, timeout=180)
    r.raise_for_status()
    rows = r.json()["results"]["bindings"]
    ents = {}
    for b in rows:
        qid = b["item"]["value"].rsplit("/", 1)[-1]
        e = ents.setdefault(qid, {"qid": qid, "label": "", "description": "",
                                  "website": "", "logo_file": "", "lat": None,
                                  "lon": None, "admin": "", "parents": []})
        def val(key):
            return (b.get(key) or {}).get("value", "")
        e["label"] = e["label"] or val("itemLabel")
        e["description"] = e["description"] or val("itemDescription")
        e["website"] = e["website"] or val("website")
        e["logo_file"] = e["logo_file"] or val("logo")
        e["admin"] = e["admin"] or val("adminLabel")
        p = val("parentLabel")
        if p and p not in e["parents"]:
            e["parents"].append(p)
        c = val("coord")
        m = re.match(r"Point\(([-\d.]+) ([-\d.]+)\)", c)
        if m and e["lat"] is None:
            e["lon"], e["lat"] = float(m.group(1)), float(m.group(2))
    out = list(ents.values())
    WD_CACHE.write_text(json.dumps(out))
    return len(out)


def _load():
    global _entities
    with _lock:
        if _entities is None:
            _entities = json.loads(WD_CACHE.read_text())
        return _entities


def _km(lat1, lon1, lat2, lon2):
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return 6371 * 2 * math.asin(math.sqrt(a))


def _loc_score(ent, city, state, lat=None, lon=None):
    desc = (ent.get("description") or "").lower()
    admin = util.norm_city(ent.get("admin") or "")
    ncity = util.norm_city(city)
    state_full = util.STATE_NAMES.get((state or "").upper(), "")
    if lat is not None and ent.get("lat") is not None:
        d = _km(lat, lon, ent["lat"], ent["lon"])
        if d <= 3:
            return 1.0
        if d <= 25:
            return 0.85
        if d > 120:
            return 0.05  # a same-name hospital far away: almost surely wrong
    if ncity and (ncity in desc or admin == ncity):
        return 0.9
    if state_full and state_full in desc:
        return 0.6
    return 0.25


def _as_result(ent, score):
    out = dict(ent)
    out["score"] = round(score, 3)
    out["parent_system"] = "; ".join(ent.get("parents") or [])
    if ent.get("logo_file"):
        out["logo_url"] = ("https://commons.wikimedia.org/wiki/Special:FilePath/"
                           + quote(ent["logo_file"]))
    return out


def match_local(account, city, state, lat=None, lon=None, min_score=0.55):
    """Match a workbook row against the local Wikidata dump."""
    acct_toks = set(util.distinctive_tokens(account))
    best, best_score = None, 0.0
    for ent in _load():
        label = ent.get("label") or ""
        name_s = util.name_similarity(account, label)
        if name_s < 0.45:
            continue
        # location can support a name match, never substitute for one
        if name_s < 0.75 and acct_toks and not (acct_toks & set(util.distinctive_tokens(label))):
            continue
        score = 0.55 * name_s + 0.45 * _loc_score(ent, city, state, lat, lon)
        if score > best_score:
            best, best_score = ent, score
    if best and best_score >= min_score:
        return _as_result(best, best_score)
    return None


def match_org(name: str, facility_state: str = None):
    """Match a health-system/organization name against the dump (no city).

    Guardrails against wrong-org matches (e.g. "Meridian Health" NJ vs
    "Meridian Health Services" IN): a fuzzy match is only trusted when the
    names are near-identical, or when the org's description/admin area is
    consistent with the facility's state. An org clearly located in a
    different state is vetoed unless the name match is essentially exact.

    Returns (entity_result, similarity) or (None, 0).
    """
    best, best_sim = None, 0.0
    for ent in _load():
        if not ent.get("website"):
            continue
        sim = util.name_similarity(name, ent.get("label") or "")
        if sim > best_sim:
            best, best_sim = ent, sim
    if not best:
        return None, 0.0

    exact = util.normalize_name(name) == util.normalize_name(best.get("label") or "")
    state_full = util.STATE_NAMES.get((facility_state or "").upper(), "")
    desc = (best.get("description") or "").lower()
    loc_text = (desc + " " + (best.get("admin") or "")).lower()
    # the different-state veto applies to FACILITIES located elsewhere, not to
    # national systems whose corporate HQ happens to be in another state
    facility_like = bool(re.search(r"(hospital|medical cent\w+|clinic) in ", desc))
    other_state = facility_like and any(s in loc_text for s in util.STATE_NAMES.values()
                                        if s and s != state_full)
    same_state = bool(state_full and state_full in loc_text)

    if other_state and not same_state and not exact:
        return None, 0.0
    if exact or best_sim >= 0.93 or (best_sim >= 0.78 and same_state):
        return _as_result(best, best_sim), best_sim
    return None, 0.0


def lookup_tail(account, city, state, min_score=0.55):
    """enwiki full-text search fallback; resolves QIDs against the local dump."""
    q = f"{account} {city} {state}".strip()
    data, _ = net.get_json(WP_API, params={
        "action": "query", "generator": "search", "gsrsearch": q,
        "gsrlimit": 5, "gsrnamespace": 0, "prop": "pageprops|description",
        "ppprop": "wikibase_item", "format": "json",
    })
    pages = ((data or {}).get("query") or {}).get("pages", {})
    by_qid = {e["qid"]: e for e in _load()}
    acct_toks = set(util.distinctive_tokens(account))
    best, best_score = None, 0.0
    for page in pages.values():
        qid = (page.get("pageprops") or {}).get("wikibase_item")
        title = page.get("title") or ""
        desc = page.get("description") or ""
        if not _HOSPITAL_WORDS.search(title + " " + desc):
            continue
        if acct_toks and not (acct_toks & set(util.distinctive_tokens(title))) \
                and util.name_similarity(account, title) < 0.75:
            continue
        ent = by_qid.get(qid)
        if ent is None:
            # not a dumped class (e.g. former hospital); build minimal entity
            ent = {"qid": qid or "", "label": title, "description": desc,
                   "website": "", "logo_file": "", "lat": None, "lon": None,
                   "admin": "", "parents": []}
        name_s = util.name_similarity(account, title)
        score = 0.55 * name_s + 0.45 * _loc_score(ent, city, state)
        if score > best_score:
            best, best_score = ent, score
    if best and best_score >= min_score:
        return _as_result(best, best_score)
    return None
