"""Step 1a: read the public sitemap and save the list of job URLs.

Run from the project root:  python -m src.collection.get_urls

The list is MERGED with the previous one, never replaced: postings expire and leave the
sitemap, but we still need their URL and dates for pages already downloaded.
For every URL we also keep first_listed / last_listed (the first and last day the job was
in the sitemap). That is what tells us how long a posting stayed online.
"""
import json
import re
from datetime import datetime, timezone

import requests

import config


def main():
    config.INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    r = requests.get(config.SITEMAP_URL, headers={"User-Agent": config.USER_AGENT}, timeout=60)
    r.raise_for_status()

    entries = re.findall(r"<url>\s*<loc>(.*?)</loc>\s*<lastmod>(.*?)</lastmod>", r.text, re.S)
    current = {u: d for u, d in entries if "/job/" in u}
    if not current:
        raise SystemExit("No job URLs found in the sitemap: not touching the saved list.")

    merged = {}
    if config.JOB_URLS.exists():
        with open(config.JOB_URLS, encoding="utf-8") as f:
            for j in json.load(f):
                day = j["lastmod"][:10]
                # entries saved before this feature: approximate with the sitemap's lastmod
                merged[j["url"]] = {"url": j["url"], "lastmod": j["lastmod"],
                                    "first_listed": j.get("first_listed") or day,
                                    "last_listed": j.get("last_listed") or day}
    before = len(merged)
    for url, lastmod in current.items():
        prev = merged.get(url)
        merged[url] = {"url": url, "lastmod": lastmod,
                       "first_listed": prev["first_listed"] if prev else today, "last_listed": today}

    with open(config.JOB_URLS, "w", encoding="utf-8") as f:
        json.dump(list(merged.values()), f, ensure_ascii=False, indent=1)

    gone = sum(1 for u in merged if u not in current)
    print(f"In sitemap now: {len(current)} | known before: {before} | new: {len(merged) - before} | "
          f"no longer listed: {gone} | total kept: {len(merged)}")
    print("Saved to", config.JOB_URLS)


if __name__ == "__main__":
    main()
