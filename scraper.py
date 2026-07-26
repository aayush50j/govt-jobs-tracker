"""
scraper.py
----------
Pulls "latest jobs" listings from sarkari-job aggregator sites and writes
a normalized jobs.json that the dashboard (index.html) reads.

Runs automatically every day via .github/workflows/update-jobs.yml
(GitHub Actions), so you never have to run it by hand.

If a site changes its HTML layout, the regex/selectors below may need a
small tweak — the parsing is written defensively (multiple fallbacks) but
no scraper is 100% future-proof against a website redesign.
"""

import json
import re
import sys
from datetime import datetime, date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

OUTPUT_FILE = Path(__file__).parent / "jobs.json"
TODAY = date.today()

# Sites to pull from. Add / remove entries here to change sources.
SOURCES = [
    {
        "name": "SarkariResult",
        "url": "https://sarkariresult.com.cm/latest-jobs/",
        "base": "https://sarkariresult.com.cm",
    },
    {
        "name": "FreeJobAlert",
        "url": "https://www.freejobalert.com/",
        "base": "https://www.freejobalert.com",
    },
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# Matches things like "17 August 2026", "17 Aug 2026", "17-08-2026"
DATE_PATTERNS = [
    re.compile(r"(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})"),
    re.compile(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})"),
]

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def parse_date_near(text):
    """Try to find a date in a chunk of text, return ISO string or None."""
    for pat in DATE_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        groups = m.groups()
        try:
            if groups[1].isalpha():
                day, mon_txt, year = groups
                month = MONTHS.get(mon_txt.lower()[:3] if len(mon_txt) > 4 else mon_txt.lower())
                if not month:
                    continue
                return date(int(year), month, int(day)).isoformat()
            else:
                day, month, year = groups
                return date(int(year), int(month), int(day)).isoformat()
        except ValueError:
            continue
    return None


def guess_category(title):
    t = title.lower()
    mapping = {
        "bank": "Bank", "railway": "Railway", "rrb": "Railway", "army": "Defence",
        "navy": "Defence", "air force": "Defence", "airforce": "Defence",
        "police": "Police", "court": "Judiciary", "judge": "Judiciary",
        "nurs": "Medical", "aiims": "Medical", "psc": "State PSC",
        "teacher": "Teaching", "ntt": "Teaching", "professor": "Teaching",
        "engineer": "Engineering", "je ": "Engineering",
    }
    for key, cat in mapping.items():
        if key in t:
            return cat
    return "General"


def guess_qualification(title):
    t = title.lower()
    if any(k in t for k in ["10th", "12th", "matric", "inter"]):
        return "10th/12th Pass"
    if any(k in t for k in ["iti", "diploma", "technician", "apprentice"]):
        return "ITI / Diploma (Technical)"
    if any(k in t for k in ["engineer", "je ", "b.tech", "be "]):
        return "Engineering (Specialized)"
    if any(k in t for k in ["nurs", "medical", "physio", "b.sc nursing"]):
        return "Medical / Nursing (Specialized)"
    if any(k in t for k in ["llb", "law", "judge", "adpo"]):
        return "LLB / Law (Specialized)"
    if any(k in t for k in ["b.ed", "teacher", "tet"]):
        return "Graduate + B.Ed (Teaching)"
    if any(k in t for k in ["po", "so", "clerk", "assistant", "apprentice", "pre"]):
        return "Graduate (Any Stream)"
    return "Check Notification"


def guess_state(title):
    states = [
        "Madhya Pradesh", "Uttar Pradesh", "Bihar", "Rajasthan", "Maharashtra",
        "Gujarat", "Punjab", "Haryana", "Jharkhand", "Uttarakhand", "Delhi",
        "Karnataka", "Kerala", "Tamil Nadu", "West Bengal", "Odisha", "Assam",
        "MP ", "UP ",
    ]
    for s in states:
        if s.lower().replace(" ", "") in title.lower().replace(" ", ""):
            return "Madhya Pradesh" if s.startswith("MP") else ("Uttar Pradesh" if s.startswith("UP") else s)
    return "All India / Central"


def scrape_source(source):
    jobs = []
    try:
        resp = requests.get(source["url"], headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[WARN] could not fetch {source['name']}: {e}", file=sys.stderr)
        return jobs

    soup = BeautifulSoup(resp.text, "html.parser")

    # Strategy: look at every <a> tag; if the link text looks like a job
    # title and either the link text or its parent block contains a date,
    # treat it as a job listing.
    seen_urls = set()
    for a in soup.find_all("a", href=True):
        title = a.get_text(strip=True)
        href = a["href"]

        if not title or len(title) < 12:
            continue
        if not any(k in title.lower() for k in [
            "recruitment", "online form", "bharti", "vacancy", "notification",
            "posts", "post ", "intake", "course",
        ]):
            continue

        if href.startswith("/"):
            href = source["base"] + href
        if href in seen_urls or source["base"] not in href:
            continue

        # look for a date in the link text, or its parent element's text
        context = title
        parent = a.find_parent(["li", "tr", "div", "p"])
        if parent:
            context = parent.get_text(" ", strip=True)

        last_date = parse_date_near(context)
        if not last_date:
            continue  # skip entries we can't confidently date

        # skip anything already expired
        if date.fromisoformat(last_date) <= TODAY:
            continue

        seen_urls.add(href)
        jobs.append({
            "title": title,
            "posts": "Various Posts",
            "category": guess_category(title),
            "state": guess_state(title),
            "qualification": guess_qualification(title),
            "lastDate": last_date,
            "posted": TODAY.isoformat(),  # first time we see it = "posted" today
            "url": href,
            "source": source["name"],
        })

    return jobs


def merge_with_existing(new_jobs):
    """Keep 'posted' date stable for jobs we've already seen before,
    so the 'latest jobs' section reflects true first-seen date, not
    today's scrape date, for jobs that keep reappearing."""
    if not OUTPUT_FILE.exists():
        return new_jobs

    try:
        old_jobs = {j["url"]: j for j in json.loads(OUTPUT_FILE.read_text())}
    except Exception:
        old_jobs = {}

    for j in new_jobs:
        old = old_jobs.get(j["url"])
        if old and "posted" in old:
            j["posted"] = old["posted"]

    return new_jobs


def main():
    all_jobs = []
    for source in SOURCES:
        found = scrape_source(source)
        print(f"{source['name']}: found {len(found)} active jobs")
        all_jobs.extend(found)

    # de-duplicate by URL
    dedup = {j["url"]: j for j in all_jobs}
    final_jobs = merge_with_existing(list(dedup.values()))
    final_jobs.sort(key=lambda j: j["lastDate"])

    OUTPUT_FILE.write_text(json.dumps({
        "generatedAt": datetime.utcnow().isoformat() + "Z",
        "jobs": final_jobs,
    }, indent=2, ensure_ascii=False))

    print(f"Wrote {len(final_jobs)} jobs to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
