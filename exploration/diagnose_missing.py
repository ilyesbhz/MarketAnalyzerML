import gzip
import json
import os
from collections import Counter

import pandas as pd
from bs4 import BeautifulSoup

df = pd.read_csv("jobs_clean.csv", dtype={"job_id": str})
bad = df[df.title.isna()]
print("Rows without title:", len(bad), "of", len(df))
print("  of which description >= 100 chars:", int((bad.desc_len >= 100).sum()))


def read_raw(jid):
    for p in (f"raw/{jid}.html.gz", f"raw/{jid}.html"):
        if os.path.exists(p):
            opener = gzip.open if p.endswith(".gz") else open
            with opener(p, "rt", encoding="utf-8") as f:
                return f.read()


outcomes = Counter()
examples = []
for jid in bad.job_id:
    html = read_raw(jid)
    soup = BeautifulSoup(html, "html.parser")
    blocks = soup.find_all("script", type="application/ld+json")
    outcome = "no JSON-LD block" if not blocks else "JSON-LD present, no JobPosting"
    for b in blocks:
        try:
            data = json.loads(b.string, strict=False)
        except Exception as e:
            outcome = "JSON-LD parse error: " + str(e)[:50]
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            outcome = "JobPosting parsed OK (unexpected)"
    outcomes[outcome] += 1
    if len(examples) < 5:
        h1 = soup.find("h1")
        examples.append((jid, len(html), soup.title.get_text(strip=True)[:70] if soup.title else None,
                         h1.get_text(strip=True)[:60] if h1 else None))

print("\nOutcomes:")
for k, v in outcomes.most_common():
    print(f"  {v:4d}  {k}")
print("\nExamples (job_id, html size, <title>, <h1>):")
for e in examples:
    print(" ", e)