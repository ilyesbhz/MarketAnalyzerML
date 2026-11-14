"""Initialize the PostgreSQL database and reference tables."""
import csv

import config
from src.common.sectors import _norm  # same normalization used when mapping jobs
from src.database import db

SCHEMA_SQL = config.ROOT / "sql" / "schema.sql"
ANALYTICS_SQL = config.ROOT / "sql" / "analytics.sql"
REFERENCE_DIR = config.DATA / "reference"


def read_csv(name):
    with open(REFERENCE_DIR / name, encoding="utf-8-sig", newline="") as f:
        return [tuple(r) for r in list(csv.reader(f))[1:]]


def main():
    db.ensure_database()
    tan = read_csv("sector_map_tanitjobs.csv")
    adz = read_csv("sector_map_adzuna.csv")
    sectors = sorted({s for _, s in tan} | {s for _, s in adz})

    with db.connect() as conn:
        print(conn.execute("SELECT version()").fetchone()[0])
        conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute(ANALYTICS_SQL.read_text(encoding="utf-8"))      # the analysis views (read by the dashboards)

        for s in sectors:
            conn.execute("INSERT INTO core.dim_sector (name) VALUES (%s) "
                         "ON CONFLICT (name) DO NOTHING", (s,))
        ids = dict(conn.execute("SELECT name, sector_id FROM core.dim_sector").fetchall())

        for label, sector in tan:
            conn.execute(
                "INSERT INTO core.map_tanitjobs_category (category_key, category_label, sector_id) "
                "VALUES (%s, %s, %s) ON CONFLICT (category_key) DO UPDATE "
                "SET category_label = EXCLUDED.category_label, sector_id = EXCLUDED.sector_id",
                (_norm(label), label, ids[sector]))
        for tag, sector in adz:
            conn.execute(
                "INSERT INTO core.map_adzuna_tag (tag, sector_id) VALUES (%s, %s) "
                "ON CONFLICT (tag) DO UPDATE SET sector_id = EXCLUDED.sector_id",
                (tag, ids[sector]))

        print("\nTable row counts:")
        for t in ["core.dim_source", "core.dim_sector", "core.map_tanitjobs_category",
                  "core.map_adzuna_tag", "core.dim_company", "core.fact_job",
                  "core.job_sector", "ops.pipeline_run"]:
            n = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            print(f"  {t:<32} {n}")
    print("\nDone.")


if __name__ == "__main__":
    main()
