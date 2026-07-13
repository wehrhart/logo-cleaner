"""Fetch official websites, verify identity, and extract logo candidates."""
import base64
import json
import re
from urllib.parse import urljoin, quote

from bs4 import BeautifulSoup

from . import net, util

# candidate source weights (multiplied into confidence).
# The deliverable is a small square PROFILE ICON, so sources that are designed
# for small display (touch/manifest/site icons) outrank page logos; wide
# wordmark logos survive only as fallbacks via the aspect penalty in imaging.
W_JSONLD = 0.90
W_HEADER_IMG = 0.88
W_IMG_LOGO = 0.80
W_INLINE_SVG = 0.82
W_OG_IMAGE = 0.60
W_TWITTER = 0.56
W_APPLE_TOUCH = 0.88
W_ICON_LINK = 0.85
W_MANIFEST = 0.90
W_MS_TILE = 0.85
W_GSTATIC = 0.70
W_WIKIDATA_LOGO = 0.85


def fetch_site(url: str):
    """Fetch a homepage, following simple variants. Returns (html, final_url, error)."""
    tried = []
    variants = [url]
    if url.startswith("https://") and "://www." not in url:
        variants.append(url.replace("https://", "https://www.", 1))
    if url.startswith("http://"):
        variants.append(url.replace("http://", "https://", 1))
    for v in variants:
        res = net.fetch(v)
        tried.append(f"{v} -> {res.status or res.error}")
        if res.ok and res.content and (
            "html" in res.content_type or res.content[:200].lstrip().lower().startswith((b"<!doctype", b"<html"))
        ):
            return res.text(), res.final_url, None
    return None, None, "; ".join(tried)


def verify_site(html: str, final_url: str, account, city: str, state: str, zip_code: str):
    """Score 0..1 that this site belongs to the workbook facility (or its system).

    `account` may be a string or a list of name variants (workbook name plus
    HIFLD current/alt names, Wikidata label): renamed facilities verify against
    what the site calls itself today, not only the legacy workbook name.
    """
    variants = [account] if isinstance(account, str) else [a for a in account if a]
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = " " + re.sub(r"\s+", " ", soup.get_text(" ").lower()) + " "
    title = (soup.title.text if soup.title else "").lower()

    dom = util.registrable_domain(final_url)
    dom_core = dom.split(".")[0].replace("-", "")
    cov, dom_hit = 0.0, 0.0
    for name in variants:
        dts = util.distinctive_tokens(name) or util.name_tokens(name)
        if not dts:
            continue
        cov = max(cov, sum(1 for t in dts if t in text or t in title) / len(dts))
        if any(t in dom_core for t in dts if len(t) >= 4):
            dom_hit = 1.0
        # initialism domains: snhhealth.org for Southern New Hampshire ...
        initials = "".join(t[0] for t in util.name_tokens(name))
        if len(initials) >= 3 and (dom_core.startswith(initials[:3]) or initials in dom_core):
            dom_hit = 1.0
    account = variants[0] if variants else ""
    ncity = util.norm_city(city)
    city_hit = 1.0 if ncity and ncity in text else 0.0
    z5 = util.zip5(zip_code)
    zip_hit = 1.0 if z5 and z5 in text else 0.0
    st = (state or "").upper()
    state_full = util.STATE_NAMES.get(st, "")
    state_hit = 1.0 if (state_full and state_full in text) or re.search(rf"[ ,]{st.lower()}[ ,.]", text) else 0.0

    score = 0.45 * cov + 0.20 * city_hit + 0.10 * zip_hit + 0.10 * state_hit + 0.15 * dom_hit
    # wrong-industry veto: a human-healthcare account must not match a
    # veterinary/animal site no matter how well the location lines up
    if re.search(r"veterinar|animal hospital|animal clinic|pet clinic|pet care", text) \
            and not re.search(r"veterinar|animal|pet", (account or "").lower()):
        score = 0.0
        cov = 0.0
    detail = f"name_cov={cov:.2f} city={city_hit:.0f} zip={zip_hit:.0f} state={state_hit:.0f} domain={dom_hit:.0f} ({dom})"
    signals = {"name_cov": cov, "city": bool(city_hit), "zip": bool(zip_hit),
               "state": bool(state_hit), "domain": bool(dom_hit),
               "thin_page": len(text) < 1500,
               "healthcare_page": bool(re.search(
                   r"health|hospital|medical|clinic|patient|physician|care", text + title)),
               "parked_page": bool(re.search(
                   r"domain (is )?for sale|buy this domain|parked|coming soon|"
                   r"under construction|godaddy|namecheap|hugedomains", text + title))}
    return round(min(score, 1.0), 3), detail, signals


def _srcset_best(srcset: str):
    best_url, best_w = None, -1
    for part in (srcset or "").split(","):
        bits = part.strip().split()
        if not bits:
            continue
        u = bits[0]
        w = 0
        if len(bits) > 1 and bits[1].endswith("w"):
            try:
                w = int(bits[1][:-1])
            except ValueError:
                w = 0
        if w > best_w:
            best_url, best_w = u, w
    return best_url


def extract_candidates(html: str, base_url: str):
    """Return (candidates, site_meta). Candidates: {url, source, weight}."""
    soup = BeautifulSoup(html, "lxml")
    cands, seen = [], set()
    dom_core = util.registrable_domain(base_url).split(".")[0].replace("-", "")

    def add(url, source, weight, note=""):
        if not url:
            return
        url = url.strip()
        if url.startswith("//"):
            url = "https:" + url
        elif not url.startswith(("http", "data:")):
            url = urljoin(base_url, url)
        if url in seen or util.looks_like_bad_logo_url(url) \
                or (note and util.looks_like_bad_logo_url(note)):
            return
        seen.add(url)
        # brand-asset URLs usually carry the org's domain core (e.g. lluh in
        # logo-lluh-color.png) — prefer those over other same-tier images
        if len(dom_core) >= 3 and not url.startswith("data:") \
                and dom_core in (url.rsplit("/", 1)[-1] + " " + note).lower().replace("-", ""):
            weight = min(weight + 0.06, 0.97)
        # "-white" logo variants vanish on light UIs; prefer colored siblings
        if "white" in url.rsplit("/", 1)[-1].lower():
            weight -= 0.15
        cands.append({"url": url, "source": source, "weight": weight, "note": note})

    # 1) JSON-LD Organization logo
    org_name = ""
    for s in soup.find_all("script", type=re.compile("ld\\+json", re.I)):
        try:
            data = json.loads(s.string or "")
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
            stack.extend(x for v in node.values() if isinstance(v, list) for x in v if isinstance(x, dict))
            logo = node.get("logo")
            if isinstance(logo, dict):
                logo = logo.get("url") or logo.get("contentUrl")
            if isinstance(logo, str):
                add(logo, "jsonld_logo", W_JSONLD)
                if isinstance(node.get("name"), str):
                    org_name = org_name or node["name"]

    # 2) header/nav imgs and any img that self-identifies as a logo
    def img_url(img):
        u = img.get("src") or img.get("data-src") or img.get("data-lazy-src") or ""
        ss = img.get("srcset") or img.get("data-srcset")
        if ss:
            u = _srcset_best(ss) or u
        return u

    header_zones = soup.select("header, nav, .header, #header, .navbar, .site-header, .masthead")
    for zone in header_zones:
        for img in zone.find_all("img"):
            attrs = " ".join([img.get("alt") or "", " ".join(img.get("class") or []),
                              img.get("id") or "", img_url(img)]).lower()
            if "logo" in attrs or "brand" in attrs:
                add(img_url(img), "header_img_logo", W_HEADER_IMG, note=img.get("alt") or "")
        for svg in zone.find_all("svg"):
            raw = str(svg)
            if len(raw) <= 400:  # trivial icons (hamburger, search)
                continue
            head = raw[:300].lower()
            if any(w in head for w in ("search", "menu", "arrow", "chevron", "close",
                                       "hamburger", "caret", "play-", "social")):
                continue
            # decorative icons declare tiny intrinsic sizes; real logos don't
            m = re.search(r'viewbox="[\d.\-]+[ ,]+[\d.\-]+[ ,]+([\d.]+)[ ,]+([\d.]+)"', raw, re.I)
            if m and max(float(m.group(1)), float(m.group(2))) <= 48:
                continue
            add("data:image/svg+xml;base64," + base64.b64encode(raw.encode()).decode(),
                "inline_svg", W_INLINE_SVG)
    for img in soup.find_all("img"):
        # badges live in footers and award/accolade sections, not headers
        skip = False
        for anc in img.parents:
            if anc.name == "footer":
                skip = True
                break
            anc_cls = " ".join(anc.get("class") or []).lower() if hasattr(anc, "get") else ""
            if any(w in anc_cls for w in ("award", "accolad", "recognition", "badge", "accredit")):
                skip = True
                break
        if skip:
            continue
        attrs = " ".join([img.get("alt") or "", " ".join(img.get("class") or []),
                          img.get("id") or "", img_url(img)]).lower()
        if "logo" in attrs and not util.looks_like_bad_logo_url(attrs):
            add(img_url(img), "img_logo", W_IMG_LOGO, note=img.get("alt") or "")

    # 3) social/meta images
    for prop, source, w in (("og:image", "og_image", W_OG_IMAGE),
                            ("twitter:image", "twitter_image", W_TWITTER)):
        m = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        if m and m.get("content"):
            add(m["content"], source, w)

    # 4) PWA manifest icons (often 512x512 brand marks)
    man_link = soup.find("link", rel=lambda r: r and "manifest" in " ".join(r if isinstance(r, list) else [r]).lower())
    if man_link and man_link.get("href"):
        man_url = urljoin(base_url, man_link["href"])
        res = net.fetch(man_url)
        if res.ok:
            try:
                man = json.loads(res.text())
                icons = man.get("icons") or []
                def _sz(icon):
                    m = re.match(r"(\d+)", icon.get("sizes") or "0")
                    return int(m.group(1)) if m else 0
                icons.sort(key=_sz, reverse=True)
                if icons and _sz(icons[0]) >= 180:
                    add(urljoin(man_url, icons[0].get("src") or ""), "manifest_icon", W_MANIFEST,
                        note=icons[0].get("sizes") or "")
            except Exception:
                pass

    # 5) msapplication tile
    tile = soup.find("meta", attrs={"name": "msapplication-TileImage"})
    if tile and tile.get("content"):
        add(tile["content"], "ms_tile", W_MS_TILE)

    # 6) icons
    best_touch, best_sz = None, -1
    for link in soup.find_all("link", rel=True):
        rels = " ".join(link.get("rel")).lower()
        href = link.get("href")
        if not href:
            continue
        if "apple-touch-icon" in rels:
            sz = 0
            m = re.match(r"(\d+)", link.get("sizes") or "")
            if m:
                sz = int(m.group(1))
            if sz >= best_sz:
                best_touch, best_sz = href, sz
        elif "icon" in rels:
            m = re.match(r"(\d+)", link.get("sizes") or "")
            if m and int(m.group(1)) >= 192:
                add(href, "icon_link", W_ICON_LINK)
    if best_touch:
        add(best_touch, "apple_touch_icon", W_APPLE_TOUCH)

    # 7) Google favicon service for the (verified) domain — last resort
    dom = util.registrable_domain(base_url)
    if dom:
        add("https://t2.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON"
            f"&fallback_opts=TYPE,SIZE,URL&url=https://{quote(dom)}&size=256",
            "gstatic_favicon", W_GSTATIC)

    # multi-org directory pages (e.g. a university system listing every campus
    # logo): when a page carries many distinct "logo" images, none of them can
    # be trusted to be THIS facility's brand — keep only header/JSON-LD/icons.
    n_img_logos = sum(1 for c in cands if c["source"] == "img_logo")
    if n_img_logos > 6:
        cands = [c for c in cands if c["source"] != "img_logo"]

    og_site = soup.find("meta", attrs={"property": "og:site_name"})
    site_meta = {
        "org_name": org_name,
        "og_site_name": og_site.get("content") if og_site and og_site.get("content") else "",
    }
    cands.sort(key=lambda c: -c["weight"])
    return cands, site_meta
