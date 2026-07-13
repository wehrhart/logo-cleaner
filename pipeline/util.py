"""Text/name normalization and matching helpers."""
import re
from difflib import SequenceMatcher
from urllib.parse import urlparse

STOPWORDS = {
    "the", "of", "and", "at", "a", "an", "&", "inc", "llc", "llp", "ltd",
    "corp", "corporation", "company", "co", "dba", "for",
}

# Tokens so common in hospital names that they carry little identity signal.
GENERIC_TOKENS = {
    "hospital", "hospitals", "medical", "center", "centre", "health",
    "healthcare", "system", "clinic", "general", "care", "services",
    "facility", "campus", "institute",
}

STATE_NAMES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas",
    "CA": "california", "CO": "colorado", "CT": "connecticut", "DE": "delaware",
    "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho",
    "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas",
    "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland",
    "MA": "massachusetts", "MI": "michigan", "MN": "minnesota",
    "MS": "mississippi", "MO": "missouri", "MT": "montana", "NE": "nebraska",
    "NV": "nevada", "NH": "new hampshire", "NJ": "new jersey",
    "NM": "new mexico", "NY": "new york", "NC": "north carolina",
    "ND": "north dakota", "OH": "ohio", "OK": "oklahoma", "OR": "oregon",
    "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina",
    "SD": "south dakota", "TN": "tennessee", "TX": "texas", "UT": "utah",
    "VT": "vermont", "VA": "virginia", "WA": "washington",
    "WV": "west virginia", "WI": "wisconsin", "WY": "wyoming",
    "DC": "district of columbia", "PR": "puerto rico", "VI": "virgin islands",
    "GU": "guam", "AS": "american samoa", "MP": "northern mariana islands",
}

_ABBREV = {
    "st": "saint", "st.": "saint", "mt": "mount", "mt.": "mount",
    "hosp": "hospital", "med": "medical", "ctr": "center", "reg": "regional",
    "mem": "memorial", "comm": "community", "univ": "university",
    "hlth": "health",
}


def normalize_name(name: str) -> str:
    s = (name or "").lower()
    s = re.sub(r"[’']", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    words = [_ABBREV.get(w, w) for w in s.split()]
    return " ".join(w for w in words if w not in STOPWORDS)


def name_tokens(name: str) -> list:
    return normalize_name(name).split()


def distinctive_tokens(name: str) -> list:
    toks = [t for t in name_tokens(name) if t not in GENERIC_TOKENS and not t.isdigit()]
    return toks


def name_similarity(a: str, b: str) -> float:
    """Blend of sequence similarity and token-set overlap, 0..1."""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    seq = SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    jac = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    # distinctive-token containment: does the shorter name live inside the longer?
    da, db = set(distinctive_tokens(a)), set(distinctive_tokens(b))
    contain = 0.0
    if da and db:
        contain = len(da & db) / min(len(da), len(db))
    return max(0.55 * seq + 0.45 * jac, 0.5 * contain + 0.3 * jac + 0.2 * seq)


def zip5(v) -> str:
    digits = re.sub(r"\D", "", str(v or ""))
    return digits.zfill(5)[:5] if digits else ""


def norm_city(v: str) -> str:
    s = re.sub(r"[^a-z ]+", " ", (v or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def street_number(address: str) -> str:
    m = re.match(r"\s*(\d+)", address or "")
    return m.group(1) if m else ""


def registrable_domain(url: str) -> str:
    try:
        host = urlparse(url if "://" in str(url) else "http://" + str(url)).hostname or ""
    except Exception:
        return ""
    host = host.lower().lstrip(".")
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net", "gov", "edu"} and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


AGGREGATOR_DOMAINS = {
    "facebook.com", "instagram.com", "x.com", "twitter.com", "linkedin.com",
    "yelp.com", "healthgrades.com", "wikipedia.org", "wikidata.org",
    "indeed.com", "glassdoor.com", "medicare.gov", "usnews.com",
    "mapquest.com", "yellowpages.com", "webmd.com", "vitals.com",
    "zocdoc.com", "cms.gov", "definitivehc.com", "npiprofile.com",
    "npino.com", "caredash.com", "wellness.com", "hospitalsafetygrade.org",
    "google.com", "bing.com", "youtube.com", "doximity.com",
    "americanhospitaldirectory.com", "ahd.com", "guidestar.org",
    "propublica.org", "zoominfo.com", "dandb.com", "bloomberg.com",
}

# URL substrings that indicate an image is NOT the facility's brand logo.
BAD_IMAGE_URL_HINTS = (
    "jcaho", "joint-commission", "jointcommission", "accredit", "award",
    "badge", "magnet", "usnews", "us-news", "best-hospital", "leapfrog",
    "sprite", "hero", "banner", "background", "bg-", "placeholder",
    "blank.", "spacer", "pixel.", "avatar", "headshot", "doctor", "physician",
    "chamber", "seal-", "certif", "top100", "top-100", "blue-distinction",
    "aha-", "grade-a", "newsweek", "licensure", "campaign", "donate",
    "fundrais", "gala", "event-", "safety-grade",
    # third-party product/social logos that appear on hospital sites
    "mychart", "my-chart", "epic-", "twitter", "x-logo", "logo-x", "facebook",
    "instagram", "youtube", "linkedin", "tiktok", "social", "app-store",
    "google-play", "apple-store",
    # platform default icons that are not the hospital's brand
    "w-logo", "wordpress", "wix-", "squarespace-logo", "godaddy",
)


def looks_like_bad_logo_url(url: str) -> bool:
    u = (url or "").lower()
    return any(h in u for h in BAD_IMAGE_URL_HINTS)
