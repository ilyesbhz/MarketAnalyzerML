"""Read-only check of the units of Adzuna salaries (yearly? monthly? hourly?).

    python -m src.database.check_adzuna_salary

Adzuna returns one number per posting without saying whether it is per hour, day,
month or year. This prints how the numbers are spread, so we can decide how to treat them.
"""
from src.database import db
from src.database.summary import show

MID = "(coalesce(salary_min, salary_max) + coalesce(salary_max, salary_min)) / 2"

QUERIES = [
    ("Stated salaries per country and day first seen (n, 10th / 50th / 90th percentile)",
     f"""SELECT country, to_char(first_seen_at, 'MM-DD') AS day, count(*) AS n,
               round((percentile_cont(0.1) WITHIN GROUP (ORDER BY mid))::numeric) AS p10,
               round((percentile_cont(0.5) WITHIN GROUP (ORDER BY mid))::numeric) AS median,
               round((percentile_cont(0.9) WITHIN GROUP (ORDER BY mid))::numeric) AS p90
        FROM (SELECT country, first_seen_at, {MID} AS mid FROM core.fact_job
              WHERE source_id = 2 AND salary_kind = 'exact') t
        GROUP BY 1, 2 ORDER BY 1, 2"""),
    ("How many stated salaries fall in each size band",
     f"""SELECT country,
               CASE WHEN mid < 100 THEN '1: under 100 (hourly?)'
                    WHEN mid < 1000 THEN '2: 100-999 (daily?)'
                    WHEN mid < 5000 THEN '3: 1000-4999 (monthly?)'
                    WHEN mid < 15000 THEN '4: 5000-14999 (?)'
                    WHEN mid < 100000 THEN '5: 15000-99999 (yearly)'
                    ELSE '6: 100000+' END AS band, count(*) AS n
        FROM (SELECT country, {MID} AS mid FROM core.fact_job
              WHERE source_id = 2 AND salary_kind = 'exact') t
        GROUP BY 1, 2 ORDER BY 1, 2"""),
    ("Median stated salary by sector (only sectors with at least 15 stated salaries)",
     f"""SELECT f.country, d.name AS sector, count(*) AS n,
               round((percentile_cont(0.5) WITHIN GROUP (ORDER BY {MID}))::numeric) AS median
        FROM core.fact_job f JOIN core.dim_sector d ON d.sector_id = f.primary_sector_id
        WHERE f.source_id = 2 AND f.salary_kind = 'exact'
        GROUP BY 1, 2 HAVING count(*) >= 15 ORDER BY 1, 4"""),
    ("A random sample of postings with a stated salary under 5000",
     f"""SELECT country, left(title, 42) AS title, salary_min, salary_max, to_char(first_seen_at, 'MM-DD') AS day
        FROM core.fact_job WHERE source_id = 2 AND salary_kind = 'exact' AND {MID} < 5000
        ORDER BY random() LIMIT 12"""),
]


def main():
    with db.connect() as conn:
        for title, q in QUERIES:
            show(title, conn.execute(q))


if __name__ == "__main__":
    main()
