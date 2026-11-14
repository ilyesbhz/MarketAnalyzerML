"""Export the active job offers as jobs.json, in the shape the Next.js seed script expects.

    python -m src.export.export_jobs                    default: all active Tunisian offers (max 3000),
                                                        at most 1500 offers per international country
    python -m src.export.export_jobs --max-tn 500 --max-intl 300

Writes exports/jobs.json and, if APP_DATA_DIR is set in the .env file, <APP_DATA_DIR>/jobs.json
(the default file read by `npx prisma db seed`).

Rules, mirroring the Zod schema of prisma/seed.ts so that no offer is rejected:
 - only offers still active, not duplicates, and not older than 90 days
 - salaryMin / salaryMax: whole numbers, yearly full-time amounts (core.v_job_salary rules),
   in `currency`; left out when the unit is unclear. Open-ended brackets ("more than 3000")
   keep only salaryMin
 - city: the governorate for Tunisia (Tunis, Sfax...), the town for Adzuna
 - skills: tools named in the posting first, then the skills found by the language model (Tunisia only)
 - url: kept only when it is a valid http(s) address
"""
import argparse
import datetime
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

import config
from src.database import db

load_dotenv(config.ROOT / ".env")
EXPORT_FILE = config.ROOT / "exports" / "jobs.json"

SOURCES = {
    "tanitjobs": {"name": "Tanitjobs", "type": "SCRAPING", "country": "Tunisia"},
    "adzuna": {"name": "Adzuna", "type": "API", "country": "International"},
}
COUNTRY_NAMES = {"tn": "Tunisia", "fr": "France", "gb": "United Kingdom"}
HOURS_PER_YEAR = {"gb": 1950}          # everyone else: 1820 (see sql/schema.sql, core.v_job_salary)
FACTOR = {"year": 1, "month": 12}
MAX_SKILLS = 10

SQL = """
SELECT aj.source, f.source_job_id, aj.title, aj.company, aj.country, aj.region, aj.city, aj.sector,
       aj.date_posted, aj.first_seen, aj.tools, aj.skills, f.url,
       f.salary_min, f.salary_max, f.salary_currency, v.salary_unit
FROM analytics.jobs aj
JOIN core.fact_job f ON f.job_pk = aj.job_pk
LEFT JOIN core.v_job_salary v ON v.job_pk = aj.job_pk
WHERE aj.is_active AND (aj.age_days IS NULL OR aj.age_days <= 90)
ORDER BY aj.date_posted DESC NULLS LAST, aj.job_pk DESC
"""


def valid_url(u):
    if not u or " " in u:
        return None
    p = urlparse(u)
    return u if p.scheme in ("http", "https") and p.netloc else None


def merge_skills(tools, skills):
    out, seen = [], set()
    for s in list(tools or [])[:8] + list(skills or []):
        s = " ".join(str(s).split())
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out[:MAX_SKILLS]


def salary_fields(source, country, smin, smax, currency, unit):
    """-> (salaryMin, salaryMax, currency) as whole yearly numbers, or (None, None, None)."""
    if unit == "hour":
        factor = HOURS_PER_YEAR.get(country, 1820)
    else:
        factor = FACTOR.get(unit)
    if factor is None or (smin is None and smax is None) or not currency or len(currency) != 3:
        return None, None, None
    lo = smin if smin is not None else smax
    hi = smax if smax is not None else (smin if source == "adzuna" else None)   # open-ended Tanitjobs bracket
    to_int = lambda x: None if x is None else max(int(round(float(x) * factor)), 0)
    return to_int(lo), to_int(hi), currency


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-tn", type=int, default=3000)
    ap.add_argument("--max-intl", type=int, default=1500, help="per international country")
    args = ap.parse_args()

    records, per_country, dropped = [], {}, 0
    with db.connect() as conn:
        rows = conn.execute(SQL).fetchall()
    for (source, ext_id, title, company, country, region, city, sector, posted, first_seen,
         tools, skills, url, smin, smax, currency, unit) in rows:
        title = " ".join((title or "").split())
        if not title or not ext_id:
            dropped += 1
            continue
        if per_country.get(country, 0) >= (args.max_tn if country == "tn" else args.max_intl):
            continue
        per_country[country] = per_country.get(country, 0) + 1
        lo, hi, cur = salary_fields(source, country, smin, smax, currency, unit)
        day = posted or first_seen
        records.append({
            "source": SOURCES[source],
            "externalId": str(ext_id),
            "title": title,
            "company": company or None,
            "country": COUNTRY_NAMES.get(country, country),
            "city": (region if country == "tn" else city) or None,
            "sector": sector or None,
            "skills": merge_skills(tools, skills),
            "salaryMin": lo, "salaryMax": hi, "currency": cur,
            "postedAt": day.isoformat() if isinstance(day, (datetime.date, datetime.datetime)) else None,
            "url": valid_url(url),
        })

    targets = [EXPORT_FILE]
    if os.getenv("APP_DATA_DIR"):
        targets.append(Path(os.getenv("APP_DATA_DIR")) / "jobs.json")
    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=1)
        tmp.replace(target)
        print(f"Wrote {len(records)} offers to {target}")

    print(f"Dropped (no title / id): {dropped}")
    for c in sorted(per_country):
        name = COUNTRY_NAMES.get(c, c)
        sub = [r for r in records if r["country"] == name]
        print(f"  {name:<15} {len(sub):>5} offers | with salary {sum(r['salaryMin'] is not None for r in sub):>5} "
              f"| with skills {sum(bool(r['skills']) for r in sub):>5} | with url {sum(r['url'] is not None for r in sub):>5}")


if __name__ == "__main__":
    main()
