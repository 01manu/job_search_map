#!/usr/bin/env python3
"""Fetch open roles in Germany from each company's own careers system.

Supported public job-board APIs: Greenhouse, Lever (US + EU), Ashby, Personio,
Recruitee and SmartRecruiters. For companies without a known board the script
tries likely board names on every system, keeps the first one that has jobs in
Germany, and remembers the result in data/ats_map.json so later runs are fast.

Usage:  python scripts/fetch_jobs.py            (writes data/jobs.json)
No third-party packages needed.
"""
import html, json, re, sys, time, unicodedata, hashlib
import urllib.request, urllib.error
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPANIES = ROOT / "companies.json"
OUT = ROOT / "data" / "jobs.json"
ATS_MAP = ROOT / "data" / "ats_map.json"
RETRY_MISSING_DAYS = 7
UA = "Mozilla/5.0 (compatible; visa-jobs-map/1.0; +https://github.com)"

# ---------------------------------------------------------------- locations
def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s)

# city -> (lat, lng, aliases). Suburbs are folded into their metro area.
CITIES = {
 "Berlin": (52.520, 13.405, ["berlin", "potsdam"]),
 "Munich": (48.137, 11.575, ["munich", "munchen", "muenchen", "garching", "unterfohring", "unterfoehring", "planegg", "gilching", "ottobrunn", "taufkirchen", "grunwald", "gruenwald", "oberhaching", "martinsried", "ismaning", "neubiberg"]),
 "Hamburg": (53.551, 9.993, ["hamburg", "schenefeld"]),
 "Frankfurt": (50.110, 8.682, ["frankfurt", "eschborn", "bad homburg", "offenbach"]),
 "Cologne": (50.938, 6.960, ["cologne", "koln", "koeln", "hurth"]),
 "Düsseldorf": (51.227, 6.773, ["dusseldorf", "duesseldorf", "neuss", "ratingen"]),
 "Stuttgart": (48.775, 9.182, ["stuttgart", "leonberg", "boblingen", "sindelfingen", "ludwigsburg", "esslingen", "metzingen"]),
 "Leipzig": (51.340, 12.375, ["leipzig"]),
 "Dresden": (51.050, 13.737, ["dresden"]),
 "Hanover": (52.375, 9.732, ["hanover", "hannover"]),
 "Nuremberg": (49.452, 11.077, ["nuremberg", "nurnberg", "nuernberg", "erlangen", "furth"]),
 "Bremen": (53.079, 8.801, ["bremen"]),
 "Essen": (51.456, 7.012, ["essen", "dortmund", "bochum", "duisburg", "gelsenkirchen"]),
 "Bonn": (50.737, 7.098, ["bonn"]),
 "Mannheim": (49.487, 8.466, ["mannheim", "ludwigshafen"]),
 "Heidelberg": (49.399, 8.672, ["heidelberg", "walldorf"]),
 "Karlsruhe": (49.007, 8.404, ["karlsruhe"]),
 "Freiburg": (47.999, 7.842, ["freiburg"]),
 "Augsburg": (48.371, 10.898, ["augsburg"]),
 "Wiesbaden": (50.078, 8.240, ["wiesbaden", "mainz"]),
 "Darmstadt": (49.873, 8.651, ["darmstadt"]),
 "Münster": (51.961, 7.626, ["munster", "muenster"]),
 "Aachen": (50.776, 6.084, ["aachen"]),
 "Kiel": (54.323, 10.123, ["kiel"]),
 "Regensburg": (49.013, 12.101, ["regensburg"]),
 "Ingolstadt": (48.766, 11.425, ["ingolstadt"]),
 "Ulm": (48.401, 9.988, ["ulm"]),
 "Chemnitz": (50.827, 12.921, ["chemnitz"]),
 "Jena": (50.927, 11.589, ["jena"]),
 "Braunschweig": (52.269, 10.521, ["braunschweig", "wolfsburg"]),
 "Bielefeld": (52.030, 8.532, ["bielefeld", "gutersloh"]),
 "Offenburg": (48.473, 7.944, ["offenburg"]),
 "Heilbronn": (49.142, 9.219, ["heilbronn"]),
 "Würzburg": (49.792, 9.953, ["wurzburg", "wuerzburg"]),
 "Rostock": (54.092, 12.099, ["rostock"]),
}
ALIAS = sorted(((a, c) for c, (_, _, al) in CITIES.items() for a in al), key=lambda x: -len(x[0]))
GERMANY_WORDS = re.compile(r"\b(germany|deutschland)\b")
DE_CODE = re.compile(r"(^|[,(/|\s])DE($|[,)/|\s])")

def german_cities(loc):
    """Return the German cities in a location string, ['Germany (remote/other)'], or [] if not in Germany."""
    n, found = norm(loc), []
    for alias, city in ALIAS:
        if city not in found and re.search(r"\b" + re.escape(alias) + r"\b", n):
            found.append(city)
    if not found and (GERMANY_WORDS.search(n) or DE_CODE.search(loc or "")):
        found.append("Germany (remote/other)")
    return found

# ---------------------------------------------------------------- levels
ENTRY = re.compile(r"\b(junior|jr|graduate|grad|trainee|apprentice|apprenticeship|ausbildung|azubi|entry[- ]level|einsteiger|berufseinsteiger|new grad|associate)\b", re.I)
INTERN = re.compile(r"\b(intern|internship|praktikum|praktikant(in)?|werkstudent(in)?|working student|student|thesis|abschlussarbeit|bachelorarbeit|masterarbeit)\b", re.I)
SENIOR = re.compile(r"\b(senior|sr|lead|principal|staff|head|director|vp|vice president|chief|cto|cfo|teamlead|teamleiter|leiter|manager|architect|expert)\b", re.I)

def level(title, hint=""):
    h = norm(hint)
    if h in ("student", "internship", "intern"): return "intern"
    if h in ("entry-level", "entry level", "entry_level", "associate"): return "entry"
    if INTERN.search(title): return "intern"
    if ENTRY.search(title): return "entry"
    if SENIOR.search(title) or h in ("experienced", "mid_senior_level", "mid-senior level", "director", "executive"): return "senior"
    return "mid"

VISA = re.compile(r"\b(visa|blue card|work permit|relocation|relocate|relocating|umzug)\b", re.I)
# ---------------------------------------------------------------- language
# German counts as "required" when the ad asks for it, unless the same phrase says it's only a plus.
DE_REQ = re.compile(
    r"(fluent|fluency|business[- ]fluent|native|excellent|very good|strong|good|proficien\w*|professional|full)\W+(?:\w+\W+){0,3}?(german|deutsch)"
    r"|(german|deutsch)\W*(language\W*)?(skills?\W*)?(\(|:|-)?\W*(c1|c2|b2|native|fluent|mother tongue|muttersprach\w*)"
    r"|german\W+(is\W+)?(required|mandatory|a must|essential)"
    r"|(german|deutsch)\w*\W+(?:\w+\W+){0,2}?(speaking|speaker)"
    r"|(sehr gute|gute|fließende|fliessende|verhandlungssichere|exzellente)\W+(?:\w+\W+){0,2}?deutsch\w*"
    r"|deutsch\w*\W+(?:\w+\W+){0,2}?(in wort und schrift|fließend|fliessend|verhandlungssicher)", re.I)
DE_SOFT = re.compile(r"(plus|nice[- ]to[- ]have|advantage|advantageous|bonus|not required|not a must|no german|not necessary|optional|helpful|beneficial|desirable|preferred|asset|von vorteil|wünschenswert|wuenschenswert|willingness to learn|learn german|german classes|german courses|language course)", re.I)
DE_WORDS = re.compile(r"\b(und|der|die|das|mit|für|fuer|wir|sie|ihre|unser|unsere|sowie|eine|einen|bei|deine|dich|bist|kenntnisse|aufgaben)\b", re.I)
EN_WORDS = re.compile(r"\b(and|the|with|for|we|you|our|your|are|will|team|experience|skills)\b", re.I)
GERMAN_TITLE = re.compile(r"(\(m/w/d\)|\(w/m/d\)|\(m/w/x\)|mitarbeiter|sachbearbeiter|kaufmann|kauffrau|fachkraft|leiter|referent|werkstudent|praktikant|ausbildung|berater|entwickler|ingenieur|vertrieb|buchhalter|pflege)", re.I)

def language(title, text):
    """'en' = English ad with no German requirement, 'de' = German required or ad written in German, '' = unknown."""
    t = text or ""
    for m in DE_REQ.finditer(t):
        window = t[max(0, m.start() - 80): m.end() + 80]
        if not DE_SOFT.search(window):
            return "de"
    de, en = len(DE_WORDS.findall(t)), len(EN_WORDS.findall(t))
    if de + en >= 6:
        return "de" if de > en else "en"
    if GERMAN_TITLE.search(title or ""):
        return "de"
    return ""

def strip_html(s):
    return re.sub(r"<[^>]+>", " ", html.unescape(html.unescape(s or "")))

# ---------------------------------------------------------------- http
def get(url, timeout=20, as_text=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, text/xml, */*"})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", "replace")
                return body if as_text else json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code in (404, 400, 401, 403, 410): return None
        except Exception:
            pass
        time.sleep(1.5)
    return None

# ---------------------------------------------------------------- adapters
# Each adapter returns a list of raw jobs: dict(title, location, url, dept, date, text, lvl_hint) or None if the board doesn't exist.
def greenhouse(slug):
    d = get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    if not d or "jobs" not in d: return None
    out = []
    for j in d["jobs"]:
        locs = [(j.get("location") or {}).get("name", "")] + [o.get("location") or o.get("name", "") for o in j.get("offices", [])]
        out.append(dict(title=j.get("title", ""), location=" / ".join(x for x in locs if x), url=j.get("absolute_url", ""),
                        dept=", ".join(x.get("name", "") for x in j.get("departments", [])), date=(j.get("updated_at") or "")[:10],
                        text=strip_html(j.get("content", ""))))
    return out

def lever(slug):
    for host in ("api.lever.co", "api.eu.lever.co"):
        d = get(f"https://{host}/v0/postings/{slug}?mode=json")
        if isinstance(d, list) and d:
            out = []
            for j in d:
                cat = j.get("categories") or {}
                locs = [cat.get("location", "")] + list(cat.get("allLocations") or [])
                text = (j.get("descriptionPlain") or "") + " " + (j.get("additionalPlain") or "") + " " + " ".join(strip_html(l.get("content", "")) for l in j.get("lists", []))
                ts = j.get("createdAt")
                out.append(dict(title=j.get("text", ""), location=" / ".join(x for x in locs if x) + (" Germany" if (j.get("country") or "").upper() == "DE" else ""),
                                url=j.get("hostedUrl", ""), dept=cat.get("team", ""), text=text,
                                date=datetime.fromtimestamp(ts / 1000, timezone.utc).strftime("%Y-%m-%d") if ts else ""))
            return out
    return None

def ashby(slug):
    d = get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=false")
    if not d or "jobs" not in d: return None
    out = []
    for j in d["jobs"]:
        if j.get("isListed") is False: continue
        addr = ((j.get("address") or {}).get("postalAddress") or {})
        locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations") or []] + [addr.get("addressLocality", ""), addr.get("addressCountry", "")]
        out.append(dict(title=j.get("title", ""), location=" / ".join(x for x in locs if x), url=j.get("jobUrl") or j.get("applyUrl", ""),
                        dept=j.get("department") or j.get("team") or "", date=(j.get("publishedAt") or "")[:10], text=j.get("descriptionPlain", "")))
    return out

def personio(slug):
    for dom in ("jobs.personio.de", "jobs.personio.com"):
        body = get(f"https://{slug}.{dom}/xml?language=en", as_text=True)
        if not body or "<position" not in body: continue
        try: root = ET.fromstring(body)
        except ET.ParseError: continue
        out = []
        for p in root.iter("position"):
            t = lambda tag: (p.findtext(tag) or "").strip()
            offices = [t("office")] + [o.text or "" for o in p.findall("./additionalOffices/office")]
            text = " ".join(strip_html(v.text or "") for v in p.iter("value"))
            out.append(dict(title=t("name"), location=" / ".join(x for x in offices if x) + " Germany" * (dom.endswith(".de") and not offices[0]),
                            url=f"https://{slug}.{dom}/job/{t('id')}", dept=t("department"), date=t("createdAt")[:10], text=text, lvl_hint=t("seniority")))
        return out
    return None

def recruitee(slug):
    d = get(f"https://{slug}.recruitee.com/api/offers/")
    if not d or "offers" not in d: return None
    out = []
    for j in d["offers"]:
        locs = [j.get("location", ""), j.get("city", ""), j.get("country", ""), "Germany" if (j.get("country_code") or "").upper() == "DE" else ""] + [f"{l.get('city','')} {l.get('country','')}" for l in j.get("locations") or []]
        out.append(dict(title=j.get("title", ""), location=" / ".join(x for x in locs if x), url=j.get("careers_url", ""),
                        dept=j.get("department") or "", date=(j.get("published_at") or j.get("created_at") or "")[:10],
                        text=strip_html((j.get("description") or "") + " " + (j.get("requirements") or "")), lvl_hint=j.get("experience_code") or ""))
    return out

def smartrecruiters(slug):
    out, offset = [], 0
    while True:
        d = get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?country=de&limit=100&offset={offset}")
        if not d or "content" not in d: return out or None if offset else None
        for j in d["content"]:
            loc = j.get("location") or {}
            out.append(dict(title=j.get("name", ""), location=f"{loc.get('city','')} {loc.get('country','')} Germany" if (loc.get("country") or "").lower() == "de" else f"{loc.get('city','')} {loc.get('country','')}",
                            url=f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}", dept=(j.get("department") or {}).get("label", ""),
                            date=(j.get("releasedDate") or "")[:10], text="", lvl_hint=(j.get("experienceLevel") or {}).get("id", "")))
        offset += 100
        if offset >= d.get("totalFound", 0) or offset > 1000: break
    def detail(j):
        pid = j["url"].rsplit("/", 1)[-1]
        x = get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{pid}")
        secs = ((x or {}).get("jobAd") or {}).get("sections") or {}
        j["text"] = " ".join(strip_html((v or {}).get("text", "")) for v in secs.values() if isinstance(v, dict))
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(detail, out[:300]))
    return out if out or d.get("totalFound") == 0 else None

ADAPTERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "personio": personio, "recruitee": recruitee, "smartrecruiters": smartrecruiters}

def slug_candidates(name):
    base = re.sub(r"\(.*?\)", "", name)
    base = re.sub(r"\b(gmbh|ag|se|group|inc|ltd|gesellschaft|technologies|technology|deutschland|germany)\b", "", base, flags=re.I)
    n = norm(base).replace("&", "and").replace("°", "")
    words = re.findall(r"[a-z0-9]+", n)
    if not words: return []
    c = ["".join(words), "-".join(words)]
    full = re.findall(r"[a-z0-9]+", norm(name))
    c += ["".join(full), "-".join(full)]
    out = []
    for x in c:
        if x and x not in out and len(x) > 2: out.append(x)
    return out

def in_germany(raw):
    return [j for j in raw if german_cities(j["location"])]

def discover(company):
    """Try every system with likely slugs; return (ats, slug, raw_jobs) for the board with most German jobs."""
    best = None
    for ats, fn in ADAPTERS.items():
        for slug in slug_candidates(company["name"]):
            raw = fn(slug if ats != "smartrecruiters" else slug.replace("-", ""))
            if raw:
                de = in_germany(raw)
                if de and (not best or len(de) > len(best[2])):
                    best = (ats, slug if ats != "smartrecruiters" else slug.replace("-", ""), raw)
                break  # board exists on this system; don't try other spellings here
    return best

# ---------------------------------------------------------------- main
def process(company, ats_map, today):
    name = company["name"]
    hint = company.get("ats")
    if hint == "none": return company, None, []
    entry = ats_map.get(name)
    tried = []
    if hint:
        ats, slug = hint.split(":", 1); tried.append((ats, slug))
    if entry and entry.get("ats"):
        tried.append((entry["ats"], entry["slug"]))
    for ats, slug in tried:
        raw = ADAPTERS[ats](slug)
        if raw is not None:
            return company, {"ats": ats, "slug": slug, "checked": today}, raw
    if entry and not entry.get("ats"):
        last = datetime.fromisoformat(entry["checked"])
        if (datetime.fromisoformat(today) - last).days < RETRY_MISSING_DAYS:
            return company, entry, []
    found = discover(company)
    if found:
        return company, {"ats": found[0], "slug": found[1], "checked": today}, found[2]
    return company, {"ats": None, "checked": today}, []

def jitter(name, city):
    h = hashlib.md5((name + city).encode()).digest()
    return (h[0] / 255 - 0.5) * 0.05, (h[1] / 255 - 0.5) * 0.08

def board_url(ats, slug):
    return {"greenhouse": f"https://job-boards.greenhouse.io/{slug}", "lever": f"https://jobs.lever.co/{slug}", "ashby": f"https://jobs.ashbyhq.com/{slug}",
            "personio": f"https://{slug}.jobs.personio.de", "recruitee": f"https://{slug}.recruitee.com", "smartrecruiters": f"https://jobs.smartrecruiters.com/{slug}"}[ats]

def main():
    companies = json.loads(COMPANIES.read_text())
    ats_map = json.loads(ATS_MAP.read_text()) if ATS_MAP.exists() else {}
    today = datetime.now(timezone.utc).date().isoformat()
    only = set(sys.argv[1:])
    if only: companies = [c for c in companies if c["name"] in only]

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda c: process(c, ats_map, today), companies))

    out_companies, jobs, found = [], [], 0
    for company, entry, raw in results:
        if entry is not None: ats_map[company["name"]] = entry
        ats = entry.get("ats") if entry else None
        ci = len(out_companies)
        careers = company.get("careers") or (board_url(ats, entry["slug"]) if ats else "")
        seen, cities = set(), {}
        for j in raw:
            jc = german_cities(j["location"])
            if not jc or not j.get("title"): continue
            key = (j["title"], j.get("url"))
            if key in seen: continue
            seen.add(key)
            url = j.get("url") or careers
            if not url.startswith("https://"): url = careers
            jobs.append({"c": ci, "t": j["title"].strip(), "cities": jc, "loc": j["location"][:120], "url": url,
                         "lv": level(j["title"], j.get("lvl_hint", "")), "dept": (j.get("dept") or "")[:60],
                         "date": j.get("date", ""), "vm": bool(VISA.search(j.get("text") or "")),
                         "lang": language(j["title"], j.get("text") or "")})
            for city in jc: cities[city] = cities.get(city, 0) + 1
        if raw: found += 1
        pins = []
        for city in cities:
            if city in CITIES:
                lat, lng, _ = CITIES[city]; dy, dx = jitter(company["name"], city)
                pins.append([city, round(lat + dy, 4), round(lng + dx, 4)])
        out_companies.append({"name": company["name"], "visa": company.get("visa", False), "offer": company.get("offer", ""),
                              "sector": company.get("sector", ""), "hq": company.get("city", ""), "careers": careers, "ats": ats or "", "pins": pins})

    OUT.parent.mkdir(exist_ok=True)
    payload = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
               "cities": {c: [v[0], v[1]] for c, v in CITIES.items()}, "companies": out_companies, "jobs": jobs}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    ATS_MAP.write_text(json.dumps(dict(sorted(ats_map.items())), indent=1, ensure_ascii=False))
    print(f"{len(jobs)} jobs in Germany from {found} job boards ({len(companies)} companies checked)")

if __name__ == "__main__":
    main()