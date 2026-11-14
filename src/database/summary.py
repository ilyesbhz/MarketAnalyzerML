"""Print database summary statistics."""
from src.database import db

QUERIES = [
    ("Jobs per source and country",
     "SELECT s.name, f.country, count(*) FROM core.fact_job f JOIN core.dim_source s USING (source_id) "
     "GROUP BY 1, 2 ORDER BY 1, 2"),
    ("Salary kinds per country",
     "SELECT country, salary_kind, count(*) FROM core.fact_job GROUP BY 1, 2 ORDER BY 1, 3 DESC"),
    ("Salary units detected (stated salaries only)",
     "SELECT country, salary_unit AS unit, count(*) AS jobs FROM core.v_job_salary "
     "GROUP BY 1, 2 ORDER BY 1, 3 DESC"),
    ("Yearly full-time-equivalent salary (usable = unit clear). Never compare currencies directly",
     "SELECT country, salary_currency AS currency, count(*) AS stated, count(salary_annual) AS usable, "
     "round((percentile_cont(0.5) WITHIN GROUP (ORDER BY salary_annual))::numeric) AS median_per_year "
     "FROM core.v_job_salary GROUP BY 1, 2 ORDER BY 1"),
    ("Top 6 sectors per country",
     "SELECT country, sector, n FROM ("
     "  SELECT f.country, d.name AS sector, count(*) AS n, "
     "         row_number() OVER (PARTITION BY f.country ORDER BY count(*) DESC) AS rk "
     "  FROM core.job_sector js JOIN core.fact_job f USING (job_pk) "
     "  JOIN core.dim_sector d USING (sector_id) GROUP BY 1, 2) t WHERE rk <= 6 ORDER BY 1, 3 DESC"),
    ("Flags (duplicates, expired)",
     "SELECT country, count(*) FILTER (WHERE is_duplicate) AS duplicates, "
     "count(*) FILTER (WHERE valid_through < current_date) AS expired, count(*) AS total "
     "FROM core.fact_job GROUP BY 1 ORDER BY 1"),
    ("Companies",
     "SELECT count(*) AS companies, count(*) FILTER (WHERE is_anonymous) AS anonymous FROM core.dim_company"),
    ("Extraction coverage",
     "SELECT count(*) FILTER (WHERE e.job_pk IS NOT NULL) AS extracted, "
     "count(*) FILTER (WHERE e.job_pk IS NULL AND NOT f.is_duplicate AND coalesce(f.desc_len,0) >= 100) AS still_to_do, "
     "count(*) FILTER (WHERE e.parse_failed) AS parse_failed FROM core.fact_job f "
     "LEFT JOIN core.job_extraction e USING (job_pk) WHERE f.source_id = 1"),
    ("Seniority and remote (extracted)",
     "SELECT seniority, remote, count(*) FROM core.job_extraction GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 12"),
    ("Top 15 named tools",
     "SELECT t AS tool, count(*) AS jobs FROM core.job_extraction, unnest(tools) AS t "
     "GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 15"),
    ("Last 6 pipeline runs",
     "SELECT run_id, step, status, rows_in, rows_out, to_char(started_at, 'YYYY-MM-DD HH24:MI') AS started, "
     "coalesce(left(note, 60), '') AS note FROM ops.pipeline_run ORDER BY run_id DESC LIMIT 6"),
]


def show(title, cur):
    cols = [d.name for d in cur.description]
    rows = [[str(x) for x in r] for r in cur.fetchall()]
    widths = [max(len(c), *(len(r[i]) for r in rows)) if rows else len(c) for i, c in enumerate(cols)]
    print(f"\n--- {title} ---")
    print("  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    for r in rows:
        print("  ".join(v.ljust(w) for v, w in zip(r, widths)))


def main():
    with db.connect() as conn:
        for title, q in QUERIES:
            show(title, conn.execute(q))


if __name__ == "__main__":
    main()
