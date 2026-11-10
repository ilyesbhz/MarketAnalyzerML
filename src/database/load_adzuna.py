"""Load every saved Adzuna raw file (data/raw/adzuna/<day>/*.json.gz) into PostgreSQL.

    python -m src.database.load_adzuna

Idempotent: all raw files are read every time. A job seen on several days appears once
(latest version of its fields); first_seen_at / last_seen_at record the first and last day
we saw it. Only salaries stated by the employer are marked 'exact'; Adzuna's own
estimates are marked 'predicted' and must never be used as real pay.
"""
import gzip
import json
import statistics

import pandas as pd

import config
from src.common.sectors import adzuna_sector
from src.database import db
from src.database.runlog import pipeline_step

SOURCE_ID = 2
CURRENCY = {"fr": "EUR", "gb": "GBP"}
OTHER = "Other / General"

UPSERT = """
INSERT INTO core.fact_job (
    source_id, source_job_id, title, company_id, country, region, city, date_posted, valid_through,
    employment_types, languages, experience_min_years, education_level, salary_min, salary_max,
    salary_currency, salary_kind, salary_period, applicants, description, desc_len,
    primary_sector_id, url, parsed_from, is_duplicate, first_seen_at, last_seen_at)
VALUES ({ph})
ON CONFLICT (source_id, source_job_id) DO UPDATE SET
    title = EXCLUDED.title, company_id = EXCLUDED.company_id, region = EXCLUDED.region,
    city = EXCLUDED.city, date_posted = EXCLUDED.date_posted, employment_types = EXCLUDED.employment_types,
    salary_min = EXCLUDED.salary_min, salary_max = EXCLUDED.salary_max,
    salary_currency = EXCLUDED.salary_currency, salary_kind = EXCLUDED.salary_kind,
    salary_period = EXCLUDED.salary_period, description = EXCLUDED.description,
    desc_len = EXCLUDED.desc_len, primary_sector_id = EXCLUDED.primary_sector_id, url = EXCLUDED.url,
    is_duplicate = EXCLUDED.is_duplicate,
    first_seen_at = LEAST(core.fact_job.first_seen_at, EXCLUDED.first_seen_at),
    last_seen_at = GREATEST(core.fact_job.last_seen_at, EXCLUDED.last_seen_at)
""".format(ph=", ".join(["%s"] * 27))


def val(v):
    """pandas turns missing values into NaN; the database must get a real NULL (a numeric NaN
    would be stored as a fake salary). Numpy numbers become plain Python numbers."""
    if v is None or (not isinstance(v, (list, str)) and pd.isna(v)):
        return None
    return v.item() if hasattr(v, "item") else v


def flag(v):
    return str(v).strip().lower() in ("1", "true")


def read_raw():
    """-> ({job id: {'country', 'first', 'last', 'rec'}}, number of files). Latest day's version wins."""
    jobs, files = {}, 0
    for p in sorted(config.ADZUNA_RAW_DIR.glob("*/*.json.gz")):
        day = p.parent.name
        with gzip.open(p, "rt", encoding="utf-8") as f:
            rec = json.load(f)
        files += 1
        for j in rec.get("results", []):
            jid = str(j.get("id"))
            e = jobs.setdefault(jid, {"country": rec["country"], "first": day, "last": day, "rec": j})
            e["first"], e["last"], e["rec"] = min(e["first"], day), max(e["last"], day), j
    return jobs, files


def main():
    with pipeline_step("load_adzuna") as run:
        jobs, files = read_raw()
        run["rows_in"] = len(jobs)
        if not jobs:
            raise SystemExit("No Adzuna raw files found. Run the collector first.")
        unknown = {e["country"] for e in jobs.values()} - set(CURRENCY)
        if unknown:
            raise SystemExit(f"No currency defined for countries {unknown}: add them to CURRENCY.")

        rows = []
        for jid, e in jobs.items():
            j = e["rec"]
            loc = j.get("location") or {}
            area = loc.get("area") or []
            smin, smax = j.get("salary_min"), j.get("salary_max")
            if smin is not None and smax is not None and smin > smax:
                smin, smax = smax, smin
            has_salary = smin is not None or smax is not None
            company = ((j.get("company") or {}).get("display_name") or "").strip()
            rows.append({
                "jid": jid, "country": e["country"], "title": (j.get("title") or "").strip(),
                "company": company, "company_key": company.lower(),
                "region": area[1] if len(area) > 1 else None,
                "city": area[-1] if len(area) > 1 else loc.get("display_name"),
                "created": (j.get("created") or "")[:10] or None,
                "types": [x for x in (j.get("contract_type"), j.get("contract_time")) if x] or None,
                "smin": smin, "smax": smax,
                "kind": ("predicted" if flag(j.get("salary_is_predicted")) else "exact") if has_salary else "none",
                "currency": CURRENCY[e["country"]] if has_salary else None,
                "period": "year" if has_salary else None,
                "description": j.get("description"), "desc_len": len(j.get("description") or ""),
                "sector": adzuna_sector((j.get("category") or {}).get("tag")),
                "url": j.get("redirect_url"), "first": e["first"], "last": e["last"],
            })
        df = pd.DataFrame(rows)
        df = df[df.title != ""].copy()
        # the same posting republished under several ids: keep the oldest, flag the others
        df["dup_key"] = df.title.str.lower() + "||" + df.company_key + "||" + df.city.fillna("").str.lower()
        df = df.sort_values(["created", "jid"])
        df["is_duplicate"] = df.duplicated("dup_key", keep="first")

        with db.connect() as conn:
            before = conn.execute("SELECT count(*) FROM core.fact_job WHERE source_id = %s",
                                  (SOURCE_ID,)).fetchone()[0]
            comp = df[df.company_key != ""].drop_duplicates("company_key")
            conn.cursor().executemany(
                "INSERT INTO core.dim_company (name, name_key, is_anonymous) VALUES (%s, %s, FALSE) "
                "ON CONFLICT (name_key) DO NOTHING", [(r.company, r.company_key) for r in comp.itertuples()])
            company_ids = dict(conn.execute("SELECT name_key, company_id FROM core.dim_company").fetchall())
            sector_ids = dict(conn.execute("SELECT name, sector_id FROM core.dim_sector").fetchall())

            params = []
            for r in df.itertuples():
                sector = r.sector if r.sector in sector_ids else OTHER
                row = (SOURCE_ID, r.jid, r.title, company_ids.get(r.company_key), r.country, r.region,
                       r.city, r.created, None, r.types, None, None, None, r.smin, r.smax, r.currency,
                       r.kind, r.period, None, r.description, r.desc_len, sector_ids[sector], r.url,
                       "adzuna_api", bool(r.is_duplicate), f"{r.first} 00:00+00", f"{r.last} 00:00+00")
                params.append(tuple(val(v) for v in row))
            conn.cursor().executemany(UPSERT, params)

            pk = dict(conn.execute("SELECT source_job_id, job_pk FROM core.fact_job WHERE source_id = %s",
                                   (SOURCE_ID,)).fetchall())
            conn.execute("DELETE FROM core.job_sector WHERE job_pk IN "
                         "(SELECT job_pk FROM core.fact_job WHERE source_id = %s)", (SOURCE_ID,))
            conn.cursor().executemany(
                "INSERT INTO core.job_sector (job_pk, sector_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                [(pk[r.jid], sector_ids[r.sector if r.sector in sector_ids else OTHER]) for r in df.itertuples()])
            after = conn.execute("SELECT count(*) FROM core.fact_job WHERE source_id = %s",
                                 (SOURCE_ID,)).fetchone()[0]
            run["rows_out"] = after

        print(f"Raw files read: {files} | distinct jobs: {len(jobs)} | in database before: {before}, "
              f"after: {after} (new: {after - before})")
        print("Days covered:", df["first"].min(), "->", df["last"].max(),
              "| duplicates flagged:", int(df.is_duplicate.sum()))
        print("\nStated salaries by size (Adzuna gives no unit: hourly / monthly / yearly are mixed;"
              " see core.v_job_salary):")
        for c, g in df.groupby("country"):
            ex = g[g.kind == "exact"]
            mid = (ex.smin.fillna(ex.smax) + ex.smax.fillna(ex.smin)) / 2
            bands = pd.cut(mid, [-1, 99.99, 999.99, 4999.99, 14999.99, 1e12],
                           labels=["<100 (hourly?)", "100-999 (?)", "1k-5k (monthly?)", "5k-15k (?)", "15k+ (yearly)"])
            counts = bands.value_counts().reindex(bands.cat.categories).to_dict()
            print(f"  {c}: {len(ex)} stated | " + " | ".join(f"{k}: {v}" for k, v in counts.items())
                  + f" | {int((g.kind == 'predicted').sum())} Adzuna estimates ignored")


if __name__ == "__main__":
    main()
