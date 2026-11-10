"""Data-quality checks on the database.

    python -m src.quality.checks

Every check runs one SQL query that returns one number and compares it with a rule.
 - severity 'error'   : the data is broken (the daily pipeline reports a failure, exit code 1)
 - severity 'warning' : something looks wrong and deserves a look, but the data is usable
Every result is stored in ops.quality_check, so quality can be followed over time.
"""
import operator
import sys

from src.database import db

OPS = {"==": operator.eq, "<=": operator.le, ">=": operator.ge}

# (name, severity, SQL returning one number, operator, threshold, why it matters)
CHECKS = [
    ("Every source has jobs", "error",
     "SELECT count(*) FROM core.dim_source s WHERE NOT EXISTS "
     "(SELECT 1 FROM core.fact_job f WHERE f.source_id = s.source_id)", "==", 0,
     "a source that loaded nothing means a broken collector or loader"),
    ("Adzuna data is fresh (days since last seen)", "warning",
     "SELECT coalesce(current_date - max(last_seen_at)::date, 999) FROM core.fact_job WHERE source_id = 2",
     "<=", 2, "the Adzuna history cannot be recovered, so a gap here is permanent"),
    ("Tanitjobs data is fresh (days since last listed)", "warning",
     "SELECT coalesce(current_date - max(last_seen_at)::date, 999) FROM core.fact_job WHERE source_id = 1",
     "<=", 3, "the daily scrape has probably stopped"),
    ("Jobs without any sector link", "error",
     "SELECT count(*) FROM core.fact_job f WHERE NOT EXISTS "
     "(SELECT 1 FROM core.job_sector js WHERE js.job_pk = f.job_pk)", "==", 0,
     "every sector analysis would silently drop these jobs"),
    ("Source and country consistent", "error",
     "SELECT count(*) FROM core.fact_job WHERE (source_id = 1 AND country <> 'tn') "
     "OR (source_id = 2 AND country NOT IN ('fr', 'gb'))", "==", 0,
     "a wrong country would mix currencies and markets"),
    ("Posting date in the future", "error",
     "SELECT count(*) FROM core.fact_job WHERE date_posted > current_date + 1", "==", 0,
     "a parsing or time-zone error"),
    ("Tanitjobs: expiry date before posting date", "warning",
     "SELECT count(*) FROM core.fact_job WHERE source_id = 1 AND valid_through < date_posted", "==", 0,
     "date parsing problem"),
    ("Jobs without a company (%)", "warning",
     "SELECT round(100.0 * count(*) FILTER (WHERE company_id IS NULL) / greatest(count(*), 1), 1) "
     "FROM core.fact_job", "<=", 5, "company analysis needs the name"),
    ("Jobs without a description (%)", "warning",
     "SELECT round(100.0 * count(*) FILTER (WHERE description IS NULL OR description = '') "
     "/ greatest(count(*), 1), 1) FROM core.fact_job", "<=", 1, "the extraction needs the text"),
    ("Jobs without a posting date (%)", "warning",
     "SELECT round(100.0 * count(*) FILTER (WHERE date_posted IS NULL) / greatest(count(*), 1), 1) "
     "FROM core.fact_job", "<=", 1, "trends over time need the date"),
    ("Duplicate postings (%)", "warning",
     "SELECT round(100.0 * count(*) FILTER (WHERE is_duplicate) / greatest(count(*), 1), 1) "
     "FROM core.fact_job", "<=", 8, "a jump means the same postings are being counted twice"),
    ("Applicants outside 0-50 (Tanitjobs caps at 50)", "warning",
     "SELECT count(*) FROM core.fact_job WHERE applicants < 0 OR applicants > 50", "==", 0,
     "the site's format may have changed"),
    ("Tanitjobs jobs waiting for extraction", "warning",
     "SELECT count(*) FROM core.fact_job f LEFT JOIN core.job_extraction e USING (job_pk) "
     "WHERE f.source_id = 1 AND NOT f.is_duplicate AND coalesce(f.desc_len, 0) >= 100 "
     "AND e.job_pk IS NULL", "<=", 150, "Ollama was probably off for more than a day"),
    ("Extraction saved with rules only (%)", "warning",
     "SELECT round(100.0 * count(*) FILTER (WHERE parse_failed) / greatest(count(*), 1), 1) "
     "FROM core.job_extraction", "<=", 2, "the language model is failing on some jobs"),
    ("Adzuna salaries with an unclear unit (%)", "warning",
     "SELECT round(100.0 * count(*) FILTER (WHERE salary_annual IS NULL) / greatest(count(*), 1), 1) "
     "FROM core.v_job_salary WHERE source_id = 2", "<=", 15, "too many salaries are unusable"),
    ("Yearly salaries above 1,000,000", "warning",
     "SELECT count(*) FROM core.v_job_salary WHERE salary_annual > 1000000", "==", 0,
     "a unit error that would distort every average"),
    ("Daily pipeline steps whose last run failed", "warning",
     "SELECT count(*) FROM (SELECT DISTINCT ON (step) step, status FROM ops.pipeline_run "
     "WHERE step LIKE 'daily:%' AND step <> 'daily:checks' ORDER BY step, started_at DESC) t "
     "WHERE status = 'failed'", "==", 0, "a step is failing and the data is going stale"),
]


def main():
    results = []
    with db.connect() as conn:
        for name, severity, sql, op, threshold, why in CHECKS:
            value = conn.execute(sql).fetchone()[0]
            ok = OPS[op](value if value is not None else 0, threshold)
            expectation = f"{op} {threshold}"
            results.append((name, severity, "pass" if ok else "fail", value, expectation, why))
            conn.execute(
                "INSERT INTO ops.quality_check (name, severity, status, value, expectation, detail) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (name, severity, "pass" if ok else "fail", value, expectation, None if ok else why))

    width = max(len(r[0]) for r in results)
    print("=== DATA QUALITY ===")
    for name, severity, status, value, expectation, why in results:
        mark = "PASS" if status == "pass" else ("FAIL" if severity == "error" else "WARN")
        print(f"  {mark}  {name:<{width}}  value {value}  (expected {expectation})")
        if status == "fail":
            print(f"        -> {why}")
    errors = sum(1 for r in results if r[2] == "fail" and r[1] == "error")
    warnings = sum(1 for r in results if r[2] == "fail" and r[1] == "warning")
    passed = sum(1 for r in results if r[2] == "pass")
    print(f"\nQuality: {passed} passed, {warnings} warnings, {errors} errors")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
