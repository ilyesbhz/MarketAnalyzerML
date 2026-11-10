import json
import os
import random
import time
from collections import Counter

import requests
from parse_job import parse_job

HEADERS = {"User-Agent": "Mozilla/5.0 (academic project; contact: your-email@example.com)"}
DELAY = 3  # seconds between requests
N = 20

with open("job_urls.json", encoding="utf-8") as f:
    all_jobs = json.load(f)

random.seed(42)
sample = random.sample(all_jobs, N)
os.makedirs("raw", exist_ok=True)

results = []
for i, item in enumerate(sample, 1):
    url = item["url"]
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
    except Exception as e:
        print(f"[{i}/{N}] ERROR {e} | {url}")
        time.sleep(DELAY)
        continue

    print(f"[{i}/{N}] {r.status_code} | {url[:80]}")
    if r.status_code == 200:
        job_id = url.split("/job/")[1].split("/")[0]
        with open(f"raw/{job_id}.html", "w", encoding="utf-8") as f:
            f.write(r.text)
        job = parse_job(r.text, url=url)
        job["lastmod"] = item["lastmod"]
        results.append(job)
    time.sleep(DELAY)

with open("jobs_test.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=1)

# --- Quick quality report ---
print("\n=== REPORT ===")
print("Parsed:", len(results), "of", N)
for field in ["title", "company", "city", "region", "category",
              "date_posted", "valid_through", "applicants", "description"]:
    missing = sum(1 for j in results if not j.get(field))
    print(f"  missing {field}: {missing}")
print("Categories:", Counter(j["category"] for j in results).most_common())
print("Regions:", Counter(j["region"] for j in results).most_common())
print("Info box keys:", Counter(k for j in results for k in j["info_box"]).most_common())
lengths = [len(j["description"] or "") for j in results]
print("Description length min/avg/max:", min(lengths), sum(lengths) // len(lengths), max(lengths))