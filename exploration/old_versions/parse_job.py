import json
import re
from bs4 import BeautifulSoup


def clean(text):
    return " ".join(text.split()) if text else None


def parse_job(html, url=None):
    soup = BeautifulSoup(html, "html.parser")
    job = {"url": url}

    # 1. Structured data (JSON-LD)
    ld = {}
    for block in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(block.string, strict=False)
        except Exception:
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            ld = data
            break

    addr = (ld.get("jobLocation") or {}).get("address") or {}
    org = ld.get("hiringOrganization") or {}
    job.update({
        "job_id": re.search(r"/job/(\d+)/", ld.get("url", url or "")).group(1)
                  if re.search(r"/job/(\d+)/", ld.get("url", url or "")) else None,
        "title": ld.get("title"),
        "company": org.get("name"),
        "city": addr.get("addressLocality"),
        "region": addr.get("addressRegion"),
        "category": ld.get("occupationalCategory"),
        "date_posted": ld.get("datePosted"),
        "valid_through": ld.get("validThrough"),
    })


        # 1b. Fallback: some pages (older template) have no JSON-LD at all
    job["parsed_from"] = "jsonld" if ld else "html"
    if not ld:
        h1 = soup.find("h1")
        job["title"] = clean(h1.get_text()) if h1 else None

        co = soup.select_one(".listing-item__info--item-company")
        job["company"] = re.sub(r"\s*-\s*$", "", clean(co.get_text())) if co else None

        loc = soup.select_one(".listing-item__info--item-location")
        region = clean(loc.get_text()).split(",")[0].strip() if loc else None
        job["region"] = region
        job["city"] = region  # the HTML only shows the region

        cat = soup.select_one(".categoy-listing h4")
        job["category"] = re.sub(r"^Offres d.emploi en\s+", "", clean(cat.get_text())) if cat else None

        job["date_posted"] = None  # filled from the sitemap lastmod in parse_all.py
        job["valid_through"] = None
        for h3 in soup.select("h3.details-body__title"):
            if "expiration" in h3.get_text().lower():
                nxt = h3.find_next_sibling("div")
                m = re.search(r"(\d{2})/(\d{2})/(\d{4})", nxt.get_text()) if nxt else None
                if m:
                    job["valid_through"] = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
                break

    # 2. Info box in the HTML (<dl><dt>label</dt><dd>value</dd></dl>)
    info = {}
    for dl in soup.select(".infos_job_details dl"):
        dt, dd = dl.find("dt"), dl.find("dd")
        if dt and dd:
            info[clean(dt.get_text()).rstrip(":").strip()] = clean(dd.get_text())

    types = info.get("Type d'emploi désiré")
    job["employment_types"] = [t.strip() for t in types.split(",")] if types else []
    job["vacancies_raw"] = info.get("Postes vacants")
    job["info_box"] = info  # keep everything for now, we'll see what other pages have

    # 3. Applicants count (may be missing on some pages)
    num = soup.select_one(".applicants-num")
    job["applicants"] = int(num.get_text(strip=True)) if num and num.get_text(strip=True).isdigit() else None

        # 4. Description from HTML: line breaks only at <br> and between <p> blocks
    desc = soup.select_one(".details-body__content.content-text")
    if desc:
        for br in desc.find_all("br"):
            br.replace_with("\n")
        for p in desc.find_all(["p", "li"]):
            p.append("\n")
        text = desc.get_text()  # no separator: inline tags stay inline
        job["description"] = "\n".join(l.strip() for l in text.splitlines() if l.strip())
    else:
        job["description"] = None
    return job

if __name__ == "__main__":
    with open("sample_job.html", encoding="utf-8") as f:
        job = parse_job(f.read(), url="https://www.tanitjobs.com/job/2059682/")
    desc = job.pop("description") or ""
    print(json.dumps(job, ensure_ascii=False, indent=2))
    print("\n--- description:", len(desc), "chars ---")
    print(desc[:600])