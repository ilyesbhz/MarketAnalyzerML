"""Download and persist Tanitjobs job pages with resumable collection."""
import argparse
import gzip
import json
import random
import time

import requests

import config


def job_id(url):
    return url.split("/job/")[1].split("/")[0]


def already_done(jid):
    return ((config.RAW_DIR / f"{jid}.html.gz").exists()
            or (config.RAW_DIR / f"{jid}.html").exists())


def save_gz(jid, text):
    tmp = config.RAW_DIR / f"{jid}.tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        f.write(text)
    tmp.replace(config.RAW_DIR / f"{jid}.html.gz")  # atomic: no half-written files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="max pages this run")
    args = ap.parse_args()

    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.JOB_URLS, encoding="utf-8") as f:
        jobs = json.load(f)

    todo = [j for j in jobs if not already_done(job_id(j["url"]))]
    random.Random(42).shuffle(todo)
    print(f"Total: {len(jobs)} | already saved: {len(jobs) - len(todo)} | to fetch: {len(todo)}")
    if args.limit:
        todo = todo[: args.limit]
        print(f"Limited to {len(todo)} pages this run")

    session = requests.Session()
    session.headers.update({"User-Agent": config.USER_AGENT})
    ok = gone = errors_in_row = 0
    stopped_on_errors = False
    start = time.time()

    try:
        for i, item in enumerate(todo, 1):
            url = item["url"]
            jid = job_id(url)
            try:
                r = session.get(url, timeout=30)
                status = r.status_code
            except requests.RequestException as e:
                status = None
                print(f"  network error: {e}")

            if status == 200:
                save_gz(jid, r.text)
                ok += 1
                errors_in_row = 0
            elif status in (404, 410):
                gone += 1
                errors_in_row = 0
                with open(config.GONE_LOG, "a", encoding="utf-8") as f:
                    f.write(f"{jid}\t{status}\t{url}\n")
            else:
                errors_in_row += 1
                print(f"  [{i}] status {status} for {jid} "
                      f"(error {errors_in_row}/{config.MAX_CONSECUTIVE_ERRORS})")
                if errors_in_row >= config.MAX_CONSECUTIVE_ERRORS:
                    print("Too many errors in a row: stopping. Try again later.")
                    stopped_on_errors = True
                    break
                time.sleep(60 * errors_in_row)

            if i % 10 == 0 or i == len(todo):
                elapsed = time.time() - start
                eta_min = (len(todo) - i) * (elapsed / i) / 60
                print(f"[{i}/{len(todo)}] ok={ok} gone={gone} | ETA ~{eta_min:.0f} min")
            time.sleep(config.SCRAPE_DELAY)
    except KeyboardInterrupt:
        print("\nStopped by user. Run again to resume.")

    print(f"Done this run: ok={ok}, gone={gone}")
    if stopped_on_errors:
        raise SystemExit(2)          # tells the daily pipeline that this step failed


if __name__ == "__main__":
    main()
