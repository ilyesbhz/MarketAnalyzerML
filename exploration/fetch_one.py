import json
import re
import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (academic project; contact: your-email@example.com)"}

# 1. Save all job URLs + lastmod from the sitemap
r = requests.get("https://www.tanitjobs.com/sitemap.xml", headers=HEADERS, timeout=60)
entries = re.findall(r"<url>\s*<loc>(.*?)</loc>\s*<lastmod>(.*?)</lastmod>", r.text, re.S)
jobs = [{"url": u, "lastmod": d} for u, d in entries if "/job/" in u]
with open("job_urls.json", "w", encoding="utf-8") as f:
    json.dump(jobs, f, ensure_ascii=False, indent=1)
print("Saved", len(jobs), "job URLs to job_urls.json")

# 2. Fetch ONE job page and save the raw HTML
url = jobs[0]["url"]
print("Fetching:", url)
page = requests.get(url, headers=HEADERS, timeout=30)
print("Status:", page.status_code, "| size:", len(page.text))
with open("sample_job.html", "w", encoding="utf-8") as f:
    f.write(page.text)

# 3. Look for structured data and the main title
soup = BeautifulSoup(page.text, "html.parser")
print("<title>:", soup.title.string.strip() if soup.title else None)
print("<h1>:", [h.get_text(strip=True) for h in soup.find_all("h1")])

ld = soup.find_all("script", type="application/ld+json")
print("JSON-LD blocks:", len(ld))
for block in ld:
    try:
        data = json.loads(block.string)
        print("  @type:", data.get("@type") if isinstance(data, dict) else type(data))
    except Exception as e:
        print("  could not parse:", e)