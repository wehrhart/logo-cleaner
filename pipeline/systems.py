"""Curated registry of major US health systems -> official websites.

Wikidata's coverage of parent *systems* is spotty (facility classes only), so
parent-branding resolution for renamed/acquired hospitals uses this registry
as the authoritative backstop. Every entry is the system's well-known primary
domain; logos are still fetched and verified from the live site, never stored
here. Keys are matched on normalized names; tuples allow aliases.
"""
from . import util

SYSTEMS = {
    ("ascension",): "https://about.ascension.org",
    ("commonspirit", "commonspirit health"): "https://www.commonspirit.org",
    ("dignity health",): "https://www.dignityhealth.org",
    ("trinity health",): "https://www.trinity-health.org",
    ("hca", "hca healthcare"): "https://hcahealthcare.com",
    ("tenet", "tenet health", "tenet healthcare"): "https://www.tenethealth.com",
    ("community health systems", "chs"): "https://www.chs.net",
    ("lifepoint", "lifepoint health"): "https://lifepointhealth.net",
    ("scionhealth",): "https://www.scionhealth.com",
    ("providence", "providence health"): "https://www.providence.org",
    ("sutter health",): "https://www.sutterhealth.org",
    ("banner health",): "https://www.bannerhealth.com",
    ("adventhealth",): "https://www.adventhealth.com",
    ("adventist health",): "https://www.adventisthealth.org",
    ("baylor scott white", "baylor scott and white"): "https://www.bswhealth.com",
    ("intermountain", "intermountain health"): "https://intermountainhealthcare.org",
    ("ssm health",): "https://www.ssmhealth.com",
    ("mercy",): "https://www.mercy.net",
    ("bon secours",): "https://www.bonsecours.com",
    ("upmc",): "https://www.upmc.com",
    ("northwell", "northwell health"): "https://www.northwell.edu",
    ("kaiser", "kaiser permanente", "kaiser foundation"): "https://healthy.kaiserpermanente.org",
    ("cleveland clinic",): "https://my.clevelandclinic.org",
    ("mayo clinic",): "https://www.mayoclinic.org",
    ("prime healthcare",): "https://www.primehealthcare.com",
    ("universal health services", "uhs"): "https://www.uhs.com",
    ("rwjbarnabas", "rwjbarnabas health", "barnabas health"): "https://www.rwjbh.org",
    ("hackensack meridian", "hackensack meridian health", "meridian health nj"): "https://www.hackensackmeridianhealth.org",
    ("atrium health",): "https://atriumhealth.org",
    ("novant health",): "https://www.novanthealth.org",
    ("wellspan",): "https://www.wellspan.org",
    ("penn medicine",): "https://www.pennmedicine.org",
    ("geisinger",): "https://www.geisinger.org",
    ("sanford health",): "https://www.sanfordhealth.org",
    ("essentia health",): "https://www.essentiahealth.org",
    ("avera",): "https://www.avera.org",
    ("christus", "christus health"): "https://www.christushealth.org",
    ("memorial hermann",): "https://memorialhermann.org",
    ("methodist le bonheur",): "https://www.methodisthealth.org",
    ("ochsner", "ochsner health"): "https://www.ochsner.org",
    ("piedmont", "piedmont healthcare"): "https://www.piedmont.org",
    ("ballad health",): "https://www.balladhealth.org",
    ("duke health",): "https://www.dukehealth.org",
    ("corewell", "corewell health"): "https://corewellhealth.org",
    ("henry ford", "henry ford health"): "https://www.henryford.com",
    ("mclaren", "mclaren health care"): "https://www.mclaren.org",
    ("munson healthcare",): "https://www.munsonhealthcare.org",
    ("froedtert",): "https://www.froedtert.com",
    ("advocate health", "advocate aurora"): "https://www.advocatehealth.org",
    ("endeavor health",): "https://www.endeavorhealth.org",
    ("osf", "osf healthcare"): "https://www.osfhealthcare.org",
    ("multicare",): "https://www.multicare.org",
    ("peacehealth",): "https://www.peacehealth.org",
    ("legacy health",): "https://www.legacyhealth.org",
    ("scripps", "scripps health"): "https://www.scripps.org",
    ("sharp healthcare",): "https://www.sharp.com",
    ("memorialcare",): "https://www.memorialcare.org",
    ("sentara", "sentara health"): "https://www.sentara.com",
    ("inova",): "https://www.inova.org",
    ("christianacare", "christiana care"): "https://christianacare.org",
    ("wvu medicine",): "https://wvumedicine.org",
    ("vandalia health",): "https://vandaliahealth.org",
    ("bjc", "bjc healthcare"): "https://www.bjc.org",
    ("coxhealth",): "https://www.coxhealth.com",
    ("covenant health tn", "covenant health"): "https://www.covenanthealth.com",
    ("ardent health",): "https://ardenthealth.com",
    ("thedacare",): "https://www.thedacare.org",
    ("aspirus",): "https://www.aspirus.org",
    ("fairview", "fairview health"): "https://www.fairview.org",
    ("allina", "allina health"): "https://www.allinahealth.org",
    ("centracare",): "https://www.centracare.com",
    ("nuvance", "nuvance health"): "https://www.nuvancehealth.org",
    ("tower health",): "https://www.towerhealth.org",
    ("st lukes health system", "saint lukes"): "https://www.saintlukeskc.org",
    ("veterans affairs", "department of veterans affairs", "va"): "https://www.va.gov",
    ("indian health service", "ihs"): "https://www.ihs.gov",
    ("encompass health",): "https://www.encompasshealth.com",
    ("select medical", "select specialty"): "https://www.selectmedical.com",
    ("kindred", "kindred healthcare"): "https://www.kindredhealthcare.com",
    ("shriners", "shriners childrens"): "https://www.shrinerschildrens.org",
    ("emory healthcare", "emory"): "https://www.emoryhealthcare.org",
    ("wellstar",): "https://www.wellstar.org",
    ("baptist health",): "",   # ambiguous: several unrelated Baptist systems
}

_INDEX = {}
for _aliases, _site in SYSTEMS.items():
    for _a in _aliases:
        _INDEX[util.normalize_name(_a)] = (_aliases[0], _site)


def lookup(name: str):
    """Exact normalized-name lookup. Returns (canonical_name, website) or None.
    Ambiguous entries (empty website) return None on purpose."""
    key = util.normalize_name(name or "")
    hit = _INDEX.get(key)
    if hit and hit[1]:
        return hit
    return None
