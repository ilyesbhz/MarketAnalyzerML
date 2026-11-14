import os

import psycopg
from dotenv import load_dotenv
from psycopg import conninfo, sql

import config

load_dotenv(config.ROOT / ".env")


def database_url():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("Missing DATABASE_URL in the .env file (see src/database/db.py).")
    return url


def connect(**kwargs):
    return psycopg.connect(database_url(), **kwargs)


def ensure_database():
    """Create the target database if it does not exist yet."""
    info = conninfo.conninfo_to_dict(database_url())
    name = info["dbname"]
    admin = {**info, "dbname": "postgres"}
    with psycopg.connect(**admin, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            print("Created database:", name)
        else:
            print("Database already exists:", name)
