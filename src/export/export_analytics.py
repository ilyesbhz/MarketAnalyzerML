"""Export the analytics views as JSON files, ready to be read by the Next.js dashboard.

    python -m src.export.export_analytics

Writes to  exports/analytics/*.json  and, if APP_DATA_DIR is set in the .env file, also to
<APP_DATA_DIR>/analytics/  (for example the data folder of the Next.js project).
The files are rewritten completely and atomically, so the app never reads a half-written file.
"""
import datetime
import decimal
import json
import os
import shutil
from datetime import timezone

from dotenv import load_dotenv

import config
from src.database import db

load_dotenv(config.ROOT / ".env")
EXPORT_DIR = config.ROOT / "exports" / "analytics"

VIEWS = {
    "market_overview": "SELECT * FROM analytics.market_overview ORDER BY country",
    "sector_summary": "SELECT * FROM analytics.sector_summary ORDER BY country, sector",
    "tool_demand": "SELECT * FROM analytics.tool_demand WHERE jobs >= 3 ORDER BY country, sector, jobs DESC",
    "seniority_mix": "SELECT * FROM analytics.seniority_mix ORDER BY country, sector, jobs DESC",
    "salary_by_seniority": "SELECT * FROM analytics.salary_by_seniority ORDER BY country, avg_pay_percentile NULLS LAST",
    "region_summary": "SELECT * FROM analytics.region_summary ORDER BY country, jobs DESC",
    # today is excluded: its count is still growing
    "daily_trend": "SELECT * FROM analytics.daily_trend WHERE day < current_date ORDER BY day",
    # the three countries side by side, one row per sector
    "pay_percentile_by_sector": (
        "SELECT sector, "
        "max(avg_pay_percentile) FILTER (WHERE country = 'tn') AS tn, "
        "max(avg_pay_percentile) FILTER (WHERE country = 'fr') AS fr, "
        "max(avg_pay_percentile) FILTER (WHERE country = 'gb') AS gb "
        "FROM analytics.sector_summary WHERE sector IS NOT NULL GROUP BY sector "
        "HAVING count(avg_pay_percentile) > 0 ORDER BY max(avg_pay_percentile) FILTER (WHERE country = 'tn') DESC NULLS LAST"),
}

CAVEATS = [
    "Salaries are yearly full-time-equivalent amounts. Tanitjobs gives monthly brackets (assumed monthly); "
    "Adzuna gives one number with no unit, so the unit is inferred from its size.",
    "Pay percentile shows where a salary sits among the stated salaries of its own country (50 = typical). "
    "It lets Tunisia, France and the UK be compared without converting currencies.",
    "Only a minority of postings state a salary (about 20% in Tunisia), so salary figures describe the postings "
    "that publish pay, not the whole market.",
    "Adzuna sector sizes depend on our sampling (the newest 50 postings per category and day): compare sectors "
    "on pay, not on number of jobs.",
    "Tools and seniority are extracted only for Tunisian postings. Adzuna descriptions are cut at 500 characters. "
    "A tool is counted only when the posting names it.",
]


def clean(v):
    if isinstance(v, decimal.Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    return v


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    tmp.replace(path)


def main():
    out, counts = {}, {}
    with db.connect() as conn:
        for name, sql in VIEWS.items():
            cur = conn.execute(sql)
            cols = [d.name for d in cur.description]
            out[name] = [{c: clean(v) for c, v in zip(cols, row)} for row in cur.fetchall()]
            counts[name] = len(out[name])
        windows = conn.execute(
            "SELECT country, min(date_posted), max(date_posted) FROM analytics.jobs GROUP BY country ORDER BY country"
        ).fetchall()

    meta = {
        "generated_at": datetime.datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rows": counts,
        "data_window": {c: {"first_posting": clean(a), "last_posting": clean(b)} for c, a, b in windows},
        "caveats": CAVEATS,
    }
    targets = [EXPORT_DIR]
    app_dir = os.getenv("APP_DATA_DIR")
    if app_dir:
        targets.append(os.path.join(app_dir, "analytics") if not str(app_dir).endswith("analytics") else app_dir)
    for target in targets:
        target = type(EXPORT_DIR)(target)
        for name, rows in out.items():
            write_json(target / f"{name}.json", rows)
        write_json(target / "meta.json", meta)
        print(f"Wrote {len(out) + 1} files to {target}")
    print("Rows:", ", ".join(f"{k}={v}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
