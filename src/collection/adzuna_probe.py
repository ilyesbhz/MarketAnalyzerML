"""Inspect Adzuna API responses and supported country codes."""
import argparse
import json
import os

import requests
from dotenv import load_dotenv

import config

load_dotenv(config.ROOT / ".env")
APP_ID = os.getenv("ADZUNA_APP_ID")
APP_KEY = os.getenv("ADZUNA_APP_KEY")
BASE = "https://api.adzuna.com/v1/api/jobs"
HEADERS = {"User-Agent": config.USER_AGENT}

ALL_CODES = ["at", "au", "be", "br", "ca", "ch", "de", "es", "fr", "gb",
             "in", "it", "mx", "nl", "nz", "pl", "sg", "us", "za", "tn"]


def search(country, what, per_page=5):
    params = {"app_id": APP_ID, "app_key": APP_KEY, "what": what,
              "results_per_page": per_page, "content-type": "application/json"}
    return requests.get(f"{BASE}/{country}/search/1", params=params,
                        headers=HEADERS, timeout=30)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe-countries", action="store_true")
    args = ap.parse_args()

    if not APP_ID or not APP_KEY:
        print("Missing ADZUNA_APP_ID / ADZUNA_APP_KEY in the .env file.")
        return

    for country in ["fr", "gb"]:
        r = search(country, "data analyst")
        print(f"\n=== {country} | status {r.status_code} ===")
        hdrs = {k: v for k, v in r.headers.items() if "limit" in k.lower() or "remaining" in k.lower()}
        print("rate-limit headers:", hdrs or "none shown")
        if r.status_code != 200:
            print(r.text[:300])
            continue
        data = r.json()
        print("total matching jobs:", data.get("count"))
        for j in data.get("results", []):
            print("-", j.get("title"), "|", (j.get("company") or {}).get("display_name"),
                  "|", (j.get("location") or {}).get("display_name"),
                  "| salary:", j.get("salary_min"), "-", j.get("salary_max"),
                  "| predicted:", j.get("salary_is_predicted"),
                  "| desc length:", len(j.get("description") or ""))
        if country == "fr" and data.get("results"):
            print("\nFull fields of one result:")
            print(json.dumps(data["results"][0], ensure_ascii=False, indent=1)[:1500])

    if args.probe_countries:
        print("\n=== country probe ===")
        ok = []
        for c in ALL_CODES:
            r = search(c, "analyst", per_page=1)
            print(c, r.status_code)
            if r.status_code == 200:
                ok.append(c)
        print("Working codes:", ok)


if __name__ == "__main__":
    main()