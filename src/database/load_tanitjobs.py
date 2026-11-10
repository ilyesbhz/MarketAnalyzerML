"""Load data/processed/jobs_clean.csv into PostgreSQL.

    python -m src.database.load_tanitjobs

Idempotent: run it again after a new scrape and existing jobs are updated,
new jobs are added, nothing is duplicated.
"""
import pandas as pd

import config
from src.common.sectors import tanitjobs_sectors
from src.database import db
from src.database.runlog import pipeline_step

SOURCE_ID = 1
OTHER = "Other / General"
KIND = {"bracket": "bracket", "confidential": "confidential", "none": "none", "other": "none"}

UPSERT = """
INSERT INTO core.fact_job (
    source_id, source_job_id, title, company_id, country, region, city,
    date_posted, valid_through, employment_types, languages,
    experience_min_years, education_level, salary_min, salary_max, salary_currency,
    salary_kind, salary_period, applicants, description, desc_len, primary_sector_id, url,
    parsed_from, is_duplicate, first_seen_at, last_seen_at)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (source_id, source_job_id) DO UPDATE SET
    title = EXCLUDED.title, company_id = EXCLUDED.company_id, region = EXCLUDED.region,
    city = EXCLUDED.city, date_posted = EXCLUDED.date_posted,
    valid_through = EXCLUDED.valid_through, employment_types = EXCLUDED.employment_types,
    languages = EXCLUDED.languages, experience_min_years = EXCLUDED.experience_min_years,
    education_level = EXCLUDED.education_level, salary_min = EXCLUDED.salary_min,
    salary_max = EXCLUDED.salary_max, salary_currency = EXCLUDED.salary_currency,
    salary_kind = EXCLUDED.salary_kind, salary_period = EXCLUDED.salary_period,
    applicants = EXCLUDED.applicants,
    description = EXCLUDED.description, desc_len = EXCLUDED.desc_len,
    primary_sector_id = EXCLUDED.primary_sector_id, url = EXCLUDED.url,
    parsed_from = EXCLUDED.parsed_from, is_duplicate = EXCLUDED.is_duplicate,
    first_seen_at = EXCLUDED.first_seen_at, last_seen_at = EXCLUDED.last_seen_at
"""


def val(v):
    """pandas NaN -> None, numpy numbers -> plain Python."""
    if v is None or (not isinstance(v, (list, str)) and pd.isna(v)):
        return None
    return v.item() if hasattr(v, "item") else v


def seen(row, field):
    """first/last day the job was listed on the site (from get_urls); date_posted if unknown."""
    day = val(getattr(row, field, None)) or val(row.date_posted) or "1970-01-01"
    return f"{str(day)[:10]} 00:00+00"


def to_list(s):
    items = [x for x in s.split("|") if x] if isinstance(s, str) else []
    return items or None


def main():
    with pipeline_step("load_tanitjobs") as run:
        df = pd.read_csv(config.JOBS_CLEAN_CSV, dtype={"job_id": str})
        run["rows_in"] = len(df)

        with db.connect() as conn:
            before = conn.execute(
                "SELECT count(*) FROM core.fact_job WHERE source_id = %s", (SOURCE_ID,)).fetchone()[0]

            # companies
            comp = df[["company", "company_key", "is_anonymous"]].dropna(subset=["company_key"]) \
                .drop_duplicates("company_key")
            conn.cursor().executemany(
                "INSERT INTO core.dim_company (name, name_key, is_anonymous) VALUES (%s,%s,%s) "
                "ON CONFLICT (name_key) DO NOTHING",
                [(r.company, r.company_key, bool(r.is_anonymous)) for r in comp.itertuples()])
            company_ids = dict(conn.execute("SELECT name_key, company_id FROM core.dim_company").fetchall())
            sector_ids = dict(conn.execute("SELECT name, sector_id FROM core.dim_sector").fetchall())

            # jobs
            params, job_sectors, no_sector = [], {}, 0
            for r in df.itertuples():
                sectors = tanitjobs_sectors(r.categories if isinstance(r.categories, str) else "")
                if not sectors:
                    sectors = [OTHER]
                    no_sector += 1
                job_sectors[r.job_id] = sectors
                bracket = r.salary_kind == "bracket"
                params.append((
                    SOURCE_ID, r.job_id, r.title, company_ids.get(val(r.company_key)), "tn",
                    val(r.region), val(r.city), val(r.date_posted), val(r.valid_through),
                    to_list(r.employment_types), to_list(r.languages),
                    val(r.experience_min_years), val(r.education_level),
                    val(r.salary_min), val(r.salary_max), "TND" if bracket else None,
                    KIND.get(r.salary_kind, "none"), "month" if bracket else None, val(r.applicants), val(r.description),
                    val(r.desc_len), sector_ids[sectors[0]], val(r.url), val(r.parsed_from),
                    bool(r.is_duplicate), seen(r, "first_listed"), seen(r, "last_listed")))
            conn.cursor().executemany(UPSERT, params)

            # sector links (rebuilt each time so a changed mapping is picked up)
            pk = dict(conn.execute(
                "SELECT source_job_id, job_pk FROM core.fact_job WHERE source_id = %s",
                (SOURCE_ID,)).fetchall())
            conn.execute("DELETE FROM core.job_sector WHERE job_pk IN "
                         "(SELECT job_pk FROM core.fact_job WHERE source_id = %s)", (SOURCE_ID,))
            links = [(pk[j], sector_ids[s]) for j, ss in job_sectors.items() for s in ss]
            conn.cursor().executemany(
                "INSERT INTO core.job_sector (job_pk, sector_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", links)

            after = conn.execute(
                "SELECT count(*) FROM core.fact_job WHERE source_id = %s", (SOURCE_ID,)).fetchone()[0]
            run["rows_out"] = after

        print(f"Read {len(df)} rows | jobs in database before: {before}, after: {after} "
              f"(new: {after - before})")
        print(f"Companies in database: {len(company_ids)} | sector links written: {len(links)} | "
              f"jobs without a mapped sector (set to '{OTHER}'): {no_sector}")


if __name__ == "__main__":
    main()
