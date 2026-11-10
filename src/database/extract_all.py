"""Run the extraction (LLM + rules) on every usable Tanitjobs job and store the result.

    python -m src.database.extract_all --limit 20     small check
    python -m src.database.extract_all                everything not yet extracted

Resumable: jobs already in core.job_extraction are skipped, so you can stop with Ctrl+C
and run it again. Results are saved every 20 jobs. Ollama must be running.
"""
import argparse
import time

import requests

import config
from src.database import db
from src.database.runlog import pipeline_step
from src.extraction.llm_extract import extract_job
from src.extraction.rules import detect_remote, detect_tools, rule_seniority

SOURCE_ID = 1
BATCH = 20

TODO_SQL = """
SELECT f.job_pk, f.title, f.description
FROM core.fact_job f
LEFT JOIN core.job_extraction e USING (job_pk)
WHERE f.source_id = %s AND NOT f.is_duplicate AND coalesce(f.desc_len, 0) >= 100
  AND e.job_pk IS NULL
ORDER BY f.job_pk
"""

RETRY_SQL = """
SELECT f.job_pk, f.title, f.description
FROM core.fact_job f JOIN core.job_extraction e USING (job_pk)
WHERE f.source_id = %s AND e.parse_failed
ORDER BY f.job_pk
"""

MAX_CONSECUTIVE_JOB_FAILURES = 5

UPSERT = """
INSERT INTO core.job_extraction (job_pk, seniority, seniority_llm, remote, tools, skills, model, parse_failed)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (job_pk) DO UPDATE SET
    seniority = EXCLUDED.seniority, seniority_llm = EXCLUDED.seniority_llm, remote = EXCLUDED.remote,
    tools = EXCLUDED.tools, skills = EXCLUDED.skills, model = EXCLUDED.model,
    parse_failed = EXCLUDED.parse_failed, extracted_at = now()
"""


def extract_with_retry(title, description, tries=3):
    """Retry on Ollama problems. ConnectionError = Ollama not running; HTTPError (500) and
    timeouts = Ollama answered badly for this job (often recovers after a short pause)."""
    for attempt in range(1, tries + 1):
        try:
            return extract_job(title, description)
        except requests.ConnectionError:
            if attempt == tries:
                raise
            print(f"  Ollama not reachable, retrying in 30 s ({attempt}/{tries})...")
            time.sleep(30)
        except (requests.HTTPError, requests.Timeout) as e:
            if attempt == tries:
                raise
            print(f"  Ollama error ({type(e).__name__}), retrying in {15 * attempt} s ({attempt}/{tries})...")
            time.sleep(15 * attempt)


def rules_only(title, description):
    """Fallback when the model keeps failing on one job: rules give seniority, remote and tools."""
    text = f"{title}\n{description or ''}"
    return {"seniority": rule_seniority(title, description), "seniority_llm": None,
            "remote": detect_remote(text), "tools": detect_tools(text), "skills": []}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--retry-failed", action="store_true",
                    help="redo the jobs that were saved with rules only or with a parse failure")
    args = ap.parse_args()

    with pipeline_step("extract_tanitjobs") as run:
        with db.connect() as conn:
            todo = conn.execute(RETRY_SQL if args.retry_failed else TODO_SQL, (SOURCE_ID,)).fetchall()
        if args.limit:
            todo = todo[: args.limit]
        run["rows_in"] = len(todo)
        print(f"Model: {config.OLLAMA_MODEL} | jobs to extract: {len(todo)}")

        done, failed, start, in_a_row = 0, 0, time.time(), 0
        conn = db.connect()
        try:
            for i, (job_pk, title, description) in enumerate(todo, 1):
                model_name = config.OLLAMA_MODEL
                try:
                    r, st, parse_failed = extract_with_retry(title, description)
                    in_a_row = 0
                except requests.ConnectionError:
                    print("Ollama stopped answering: saving what is done. Start Ollama and run again.")
                    run["note"] = "stopped early: Ollama stopped answering"
                    break
                except (requests.HTTPError, requests.Timeout) as e:
                    in_a_row += 1
                    print(f"  job_pk={job_pk} '{(title or '')[:45]}' failed ({type(e).__name__}): "
                          f"saved with rules only ({in_a_row}/{MAX_CONSECUTIVE_JOB_FAILURES} in a row)")
                    r, parse_failed = rules_only(title, description), True
                    model_name = f"{config.OLLAMA_MODEL} [rules only]"
                failed += parse_failed
                conn.execute(UPSERT, (job_pk, r["seniority"], r["seniority_llm"], r["remote"],
                                      r["tools"], r["skills"], model_name, parse_failed))
                done += 1
                if done % BATCH == 0:
                    conn.commit()
                if in_a_row >= MAX_CONSECUTIVE_JOB_FAILURES:
                    print("Too many failures in a row: Ollama looks unhealthy. Restart the Ollama app "
                          "and run again.")
                    run["note"] = "stopped early: repeated Ollama errors"
                    break
                if i % 25 == 0 or i == len(todo):
                    eta = (len(todo) - i) * (time.time() - start) / i / 60
                    print(f"[{i}/{len(todo)}] saved {done} | failures {failed} | ETA ~{eta:.0f} min")
        except KeyboardInterrupt:
            print("\nStopped by user. Run again to resume.")
            run["note"] = "stopped by user"
        finally:
            conn.commit()
            conn.close()
            run["rows_out"] = done
        print(f"Done this run: {done} jobs extracted ({failed} parse failures).")


if __name__ == "__main__":
    main()
