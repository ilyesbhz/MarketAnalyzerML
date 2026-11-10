import argparse
import gzip
import json
import os
import random
import time

import requests

# Put a real contact email here
HEADERS = {"User-Agent": "ilyeszhb@gmail.com"}
DELAY = 3          # seconds between requests
RAW_DIR = "raw"
MAX_CONSECUTIVE_ERRORS = 5


def job_id(url):
    return url.split("/job/")[1].split("/")[0]


def already_done(jid):
    return (os.path.exists(f"{RAW_DIR}/{jid}.html.gz")
            or os.path.exists(f"{RAW_DIR}/{jid}.html"))  # from the earlier test


def save_gz(jid, text):
    tmp = f"{RAW_DIR}/{jid}.tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, f"{RAW_DIR}/{jid}.html.gz")  # atomic: no half-written files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="max pages this run")
    args = ap.parse_args()

    os.makedirs(RAW_DIR, exist_ok=True)
    with open("job_urls.json", encoding="utf-8") as f:
        jobs = json.load(f)

    todo = [j for j in jobs if not already_done(job_id(j["url"]))]
    random.Random(42).shuffle(todo)  # representative order, not just the newest
    print(f"Total: {len(jobs)} | already saved: {len(jobs) - len(todo)} | to fetch: {len(todo)}")
    if args.limit:
        todo = todo[: args.limit]
        print(f"Limited to {len(todo)} pages this run")

    session = requests.Session()
    session.headers.update(HEADERS)
    ok = gone = 0
    errors_in_row = 0
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
                with open("gone.log", "a", encoding="utf-8") as f:
                    f.write(f"{jid}\t{status}\t{url}\n")
            else:
                errors_in_row += 1
                print(f"  [{i}] status {status} for {jid} (error {errors_in_row}/{MAX_CONSECUTIVE_ERRORS})")
                if errors_in_row >= MAX_CONSECUTIVE_ERRORS:
                    print("Too many errors in a row: stopping. Try again later.")
                    break
                time.sleep(60 * errors_in_row)  # back off: 1, 2, 3... minutes

            if i % 10 == 0 or i == len(todo):
                elapsed = time.time() - start
                eta_min = (len(todo) - i) * (elapsed / i) / 60
                print(f"[{i}/{len(todo)}] ok={ok} gone={gone} | ETA ~{eta_min:.0f} min")
            time.sleep(DELAY)
    except KeyboardInterrupt:
        print("\nStopped by user. Run again to resume.")

    print(f"Done this run: ok={ok}, gone={gone}")


if __name__ == "__main__":
    main()