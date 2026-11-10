"""The daily pipeline: collect -> parse -> load -> extract, for both sources.

    python -m src.pipeline.run_daily               everything
    python -m src.pipeline.run_daily --only adzuna one branch only (tanitjobs | adzuna)

Two independent branches: if one step of a branch fails, the rest of THAT branch is
skipped but the other branch still runs. Each step is recorded in ops.pipeline_run
(prefix 'daily:') and the full output goes to logs/daily_<date>.log.
Exit code is 0 only if nothing failed.
"""
import argparse
import os
import subprocess
import sys
import threading
import time
from datetime import datetime

import requests

import config
from src.database import db

STEP_TIMEOUT = 4 * 3600   # seconds; a step that runs longer is killed and reported as failed

STEPS = [
    # (branch, name, python module, extra arguments)
    # Adzuna goes FIRST: it is quick (a few minutes) and cannot be recovered later, because the API
    # only returns today's newest postings. The Tanitjobs branch can always be caught up from the
    # sitemap (postings stay listed about 30 days), so it may safely run long.
    ("adzuna",    "collect",         "src.collection.adzuna_collect", []),
    ("adzuna",    "load_adzuna",     "src.database.load_adzuna", []),
    ("tanitjobs", "get_urls",        "src.collection.get_urls", []),
    ("tanitjobs", "scrape_new",      "src.collection.scrape_all", []),
    ("tanitjobs", "parse",           "src.parsing.parse_all", []),
    ("tanitjobs", "load_tanitjobs",  "src.database.load_tanitjobs", []),
    ("tanitjobs", "extract",         "src.database.extract_all", []),
    # data-quality checks always run, whatever happened above: they report the state of the data
    ("quality",   "checks",          "src.quality.checks", []),
]


def ollama_up():
    try:
        return requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=3).status_code == 200
    except requests.RequestException:
        return False


def log_run(step, status, seconds, note=""):
    """Write one row to ops.pipeline_run. Never lets a logging problem stop the pipeline."""
    try:
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO ops.pipeline_run (step, started_at, finished_at, status, note) "
                "VALUES (%s, now() - make_interval(secs => %s), now(), %s, %s)",
                (f"daily:{step}", seconds, status, note[:500]))
    except Exception as e:                      # database down, wrong password...
        print(f"  (could not write to ops.pipeline_run: {type(e).__name__})")


def run_module(module, args, logfile):
    """Run `python -m module`, show its output live on the screen AND write it to the log.
    Returns (exit code, last output lines). A step silent or running longer than STEP_TIMEOUT is killed."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    proc = subprocess.Popen([sys.executable, "-m", module, *args], cwd=config.ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    timed_out = []
    watchdog = threading.Timer(STEP_TIMEOUT, lambda: (timed_out.append(True), proc.kill()))
    watchdog.start()
    last = []
    try:
        for line in proc.stdout:
            logfile.write(line)
            logfile.flush()                       # the log is readable while the step is running
            print("    | " + line.rstrip(), flush=True)
            last = (last + [line.rstrip()])[-3:]
    finally:
        watchdog.cancel()
    code = proc.wait()
    if timed_out:
        return 124, f"killed after {STEP_TIMEOUT / 3600:g} h (timeout)"
    return code, " | ".join(x for x in last if x)[-300:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["tanitjobs", "adzuna", "quality"])
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

    config.ROOT.joinpath("logs").mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    results, broken = [], set()
    with open(config.ROOT / "logs" / f"daily_{stamp}.log", "a", encoding="utf-8") as logfile:
        print(f"=== daily pipeline {datetime.now():%Y-%m-%d %H:%M} ===")
        logfile.write(f"\n=== daily pipeline {datetime.now():%Y-%m-%d %H:%M} ===\n")
        for branch, name, module, extra in STEPS:
            if args.only and branch != args.only:
                continue
            t0 = time.time()
            if branch in broken:
                status, note = "skipped", f"skipped: an earlier {branch} step failed"
            elif name == "extract" and not ollama_up():
                status, note = "skipped", "skipped: Ollama is not running (jobs are extracted at the next run)"
            else:
                logfile.write(f"\n--- {name} ({module}) ---\n")
                code, tail = run_module(module, extra, logfile)
                status = "ok" if code == 0 else "failed"
                note = tail if code == 0 else f"exit code {code}: {tail}"
                if code != 0:
                    broken.add(branch)
            secs = time.time() - t0
            results.append((name, status))
            log_run(name, status, secs, note if status != "ok" else "")
            print(f"  {name:<16} {status:<8} {secs:6.0f}s  {note if status != 'ok' else ''}")
            logfile.flush()
        ok = sum(s == "ok" for _, s in results)
        bad = sum(s == "failed" for _, s in results)
        skipped = sum(s == "skipped" for _, s in results)
        line = f"Finished: {ok} ok, {bad} failed, {skipped} skipped"
        print(line)
        logfile.write(line + "\n")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
