"""Central configuration for the hospital logo pipeline.

Every value can be overridden with an environment variable (see .env.example).
A .env file at the repo root is loaded automatically if present.
"""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    p = REPO_ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()


def _env(name: str, default):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


# --- paths ---------------------------------------------------------------
OUTPUT_DIR = Path(_env("OUTPUT_DIR", REPO_ROOT / "output"))
LOGOS_DIR = OUTPUT_DIR / "logos"
REVIEW_DIR = OUTPUT_DIR / "review"
MANIFESTS_DIR = OUTPUT_DIR / "manifests"
CACHE_DIR = OUTPUT_DIR / "cache"
HTTP_CACHE_DIR = CACHE_DIR / "http"
LOGS_DIR = OUTPUT_DIR / "logs"
DB_PATH = OUTPUT_DIR / "state.db"

for _d in (LOGOS_DIR, REVIEW_DIR, MANIFESTS_DIR, CACHE_DIR, HTTP_CACHE_DIR, LOGS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

WORKBOOK_PATH = Path(_env("WORKBOOK_PATH", REPO_ROOT / "data" / "Account_Icon_Search_Batch_3.xlsx"))

# --- output naming --------------------------------------------------------
# NOTE: the task template said File_{ID}_0_0_0_0_0_0.png (six zeros) but all
# three worked examples show five zeros (File_8_0_0_0_0_0.png). We follow the
# examples; override FILENAME_TEMPLATE if six zeros are actually required.
FILENAME_TEMPLATE = _env("FILENAME_TEMPLATE", "File_{id}_0_0_0_0_0.png")

# --- image rules ----------------------------------------------------------
MIN_OUTPUT_SIZE = int(_env("MIN_OUTPUT_SIZE", "250"))
PREFERRED_OUTPUT_SIZE = int(_env("PREFERRED_OUTPUT_SIZE", "500"))
PAD_RATIO = float(_env("PAD_RATIO", "0.08"))          # padding on each side
MIN_SOURCE_DIM = int(_env("MIN_SOURCE_DIM", "96"))     # smaller sources -> review
MAX_UPSCALE = float(_env("MAX_UPSCALE", "3.0"))
MAX_ASPECT = float(_env("MAX_ASPECT", "12.0"))         # wordmarks can be wide

# --- decision thresholds ---------------------------------------------------
ACCEPT_THRESHOLD = float(_env("ACCEPT_THRESHOLD", "0.70"))
HIFLD_MATCH_THRESHOLD = float(_env("HIFLD_MATCH_THRESHOLD", "0.62"))

# --- networking -------------------------------------------------------------
CONCURRENCY = int(_env("CONCURRENCY", "8"))
HTTP_TIMEOUT = float(_env("HTTP_TIMEOUT", "20"))
MAX_RETRIES = int(_env("MAX_RETRIES", "2"))
PER_HOST_DELAY = float(_env("PER_HOST_DELAY", "1.0"))
WIKI_DELAY = float(_env("WIKI_DELAY", "0.15"))
USER_AGENT = _env(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
)

# HIFLD / ORNL national hospital dataset (identity + official website source)
HIFLD_QUERY_URL = _env(
    "HIFLD_QUERY_URL",
    "https://services2.arcgis.com/RQcpPaCpMAXzUI5g/arcgis/rest/services/"
    "US_Hospitals/FeatureServer/0/query",
)
HIFLD_CACHE = CACHE_DIR / "hifld_hospitals.json"

# Optional paid search API (unused unless a key is supplied)
SERPAPI_KEY = _env("SERPAPI_KEY", None)


def filename_for(row_id) -> str:
    return FILENAME_TEMPLATE.format(id=row_id)
