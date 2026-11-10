import re
from collections import Counter
import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (academic project; contact: ilyeszhb@gmail.com)"}

r = requests.get("https://www.tanitjobs.com/sitemap.xml", headers=HEADERS, timeout=60)
print("Status:", r.status_code, "| size:", len(r.text), "chars")

# Is this a sitemap index (pointing to more sitemaps) or a list of pages?
print("Contains <sitemapindex>:", "<sitemapindex" in r.text)

urls = re.findall(r"<loc>(.*?)</loc>", r.text)
print("Total <loc> entries:", len(urls))

# Group URLs by their first path segment (job, jobs, company, ...)
kinds = Counter(re.sub(r"https?://[^/]+/([^/]*).*", r"\1", u) for u in urls)
print("By first path segment:", kinds.most_common(10))

# Show a few examples of job pages
jobs = [u for u in urls if "/job/" in u]
print("Job URLs:", len(jobs))
for u in jobs[:5]:
    print(" ", u)

# Do entries have lastmod?
print("Entries with <lastmod>:", r.text.count("<lastmod>"))