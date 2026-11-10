"""Record every pipeline step in ops.pipeline_run (status, rows in/out, error note).

Usage:
    with pipeline_step("load_tanitjobs") as run:
        ...
        run["rows_in"] = 100
        run["rows_out"] = 98
The log is written on its own connection, so a failed step is still recorded.
"""
from contextlib import contextmanager

from src.database import db


@contextmanager
def pipeline_step(step):
    with db.connect() as conn:
        run_id = conn.execute(
            "INSERT INTO ops.pipeline_run (step) VALUES (%s) RETURNING run_id", (step,)
        ).fetchone()[0]
    info = {"rows_in": None, "rows_out": None, "note": None}
    status = "failed"
    try:
        yield info
        status = "ok"
    except BaseException as e:
        info["note"] = f"{type(e).__name__}: {e}"[:500]
        raise
    finally:
        with db.connect() as conn:
            conn.execute(
                "UPDATE ops.pipeline_run SET finished_at = now(), status = %s, rows_in = %s, "
                "rows_out = %s, note = %s WHERE run_id = %s",
                (status, info["rows_in"], info["rows_out"], info["note"], run_id))
