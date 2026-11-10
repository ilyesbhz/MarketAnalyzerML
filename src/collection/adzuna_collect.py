"""Step 1c (global): daily collector for the Adzuna API.

Goes through every Adzuna category for each country, newest postings first,
and saves the raw JSON of every call. Resumable within a day and stops when the
daily call budget is used (the API does not report remaining calls itself).

    python -m src.collection.adzuna_collect --max-calls 2     (small test)
    python -m src.collection.adzuna_collect                   (full daily run)
"""
import argparse
import gzip
import json
import os
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

import config

load_dotenv(config.ROOT / ".env")
APP_ID = os.getenv("ADZUNA_APP_ID")
APP_KEY = os.getenv("ADZUNA_APP_KEY")
BASE = "https://api.adzuna.com/v1/api/jobs"


def load_categories():
    if not config.ADZUNA_CATEGORIES_JSON.exists():
        raise SystemExit("Run first:  python -m src.collection.adzuna_categories")
    with open(config.ADZUNA_CATEGORIES_JSON, encoding="utf-8") as f:
        data = json.load(f)
    return {c: [x["tag"] for x in data.get(c, []) if x["tag"] != "unknown"]
            for c in config.ADZUNA_COUNTRIES}


def load_calls():
    if config.ADZUNA_CALLS_LOG.exists():
        with open(config.ADZUNA_CALLS_LOG, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_calls(calls):
    with open(config.ADZUNA_CALLS_LOG, "w", encoding="utf-8") as f:
        json.dump(calls, f, indent=1, sort_keys=True)


def out_path(day, country, tag, page):
    return config.ADZUNA_RAW_DIR / day / f"{country}_{tag}_p{page}.json.gz"


def save_result(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False)
    tmp.replace(path)  # atomic: no half-written files


def fetch(country, tag, page):
    params = {"app_id": APP_ID, "app_key": APP_KEY, "category": tag, "sort_by": "date",
              "results_per_page": config.ADZUNA_RESULTS_PER_PAGE,
              "content-type": "application/json"}
    return requests.get(f"{BASE}/{country}/search/{page}", params=params,
                        headers={"User-Agent": config.USER_AGENT}, timeout=30)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-calls", type=int, default=None, help="cap for this run (tests)")
    ap.add_argument("--pages", type=int, default=1, help="pages per category and country")
    args = ap.parse_args()

    if not APP_ID or not APP_KEY:
        raise SystemExit("Missing ADZUNA_APP_ID / ADZUNA_APP_KEY in the .env file.")

    day = datetime.now(timezone.utc).date().isoformat()
    cats = load_categories()
    tasks = [(c, tag, p) for p in range(1, args.pages + 1)
             for c in config.ADZUNA_COUNTRIES for tag in cats[c]]
    todo = [t for t in tasks if not out_path(day, *t).exists()]

    calls = load_calls()
    used_before = calls.get(day, 0)
    allowed = max(config.ADZUNA_DAILY_BUDGET - used_before, 0)
    if args.max_calls is not None:
        allowed = min(allowed, args.max_calls)
    month_used = sum(v for k, v in calls.items() if k[:7] == day[:7])
    print(f"Day {day} | tasks: {len(tasks)} | already saved today: {len(tasks) - len(todo)} | "
          f"to do: {len(todo)}")
    print(f"Calls used today: {used_before}/{config.ADZUNA_DAILY_BUDGET} | "
          f"this month: {month_used} | allowed this run: {allowed}")

    saved_jobs = ok = errors_in_row = sent = 0
    stop = False
    failed_run = False
    for i, (country, tag, page) in enumerate(todo, 1):
        if sent >= allowed:
            print("Call budget for this run reached: stopping.")
            break
        r = None
        for attempt in (1, 2, 3):
            if sent >= allowed:
                break
            try:
                r = fetch(country, tag, page)
            except requests.RequestException as e:
                r = None
                print(f"  network error: {e}")
            sent += 1
            calls[day] = calls.get(day, 0) + 1
            save_calls(calls)  # persisted after every request: safe if we crash
            if r is not None and r.status_code < 500:
                break
            time.sleep(10 * attempt)

        if r is None:
            errors_in_row += 1
        elif r.status_code == 200:
            data = r.json()
            results = data.get("results", [])
            record = {"fetched_at": datetime.now(timezone.utc).isoformat(),
                      "country": country, "category": tag, "page": page,
                      "params": {"sort_by": "date",
                                 "results_per_page": config.ADZUNA_RESULTS_PER_PAGE},
                      "status": 200, "count": data.get("count"),
                      "n_results": len(results), "results": results}
            save_result(out_path(day, country, tag, page), record)
            ok += 1
            saved_jobs += len(results)
            errors_in_row = 0
            print(f"[{i}/{len(todo)}] {country} {tag} p{page} -> {len(results)} results "
                  f"(total matching: {data.get('count')})")
        elif r.status_code == 429:
            print("429: rate limit reached. Stopping, try again later.")
            stop = failed_run = True
        elif r.status_code in (401, 403):
            print(f"{r.status_code}: key rejected or not allowed. Check the .env file / account.")
            stop = failed_run = True
        else:
            errors_in_row += 1
            print(f"  [{i}] status {r.status_code} for {country} {tag}: {r.text[:120]}")

        if stop:
            break
        if errors_in_row >= config.MAX_CONSECUTIVE_ERRORS:
            print("Too many errors in a row: stopping.")
            failed_run = True
            break
        time.sleep(config.ADZUNA_DELAY)

    print(f"\nDone: {ok} calls saved, {saved_jobs} job records | "
          f"calls today: {calls.get(day, 0)}/{config.ADZUNA_DAILY_BUDGET}")
    if failed_run:
        raise SystemExit(2)          # tells the daily pipeline that this step failed


if __name__ == "__main__":
    main()
