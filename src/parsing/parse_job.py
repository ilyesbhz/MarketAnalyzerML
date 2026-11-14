"""Parse a Tanitjobs page using JSON-LD data with an HTML fallback."""
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
    m = re.search(r"/job/(\d+)/", ld.get("url", url or "") or "")
    job.update({
        "job_id": m.group(1) if m else None,
        "title": ld.get("title"),
        "company": org.get("name"),
        "city": addr.get("addressLocality"),
        "region": addr.get("addressRegion"),
        "category": ld.get("occupationalCategory"),
        "date_posted": ld.get("datePosted"),
        "valid_through": ld.get("validThrough"),
    })

    # 1b. Fallback: older template without JSON-LD
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
        job["category"] = (re.sub(r"^Offres d.emploi en\s+", "", clean(cat.get_text()))
                           if cat else None)

        job["date_posted"] = None  # filled from the sitemap lastmod in parse_all
        job["valid_through"] = None
        for h3 in soup.select("h3.details-body__title"):
            if "expiration" in h3.get_text().lower():
                nxt = h3.find_next_sibling("div")
                mm = re.search(r"(\d{2})/(\d{2})/(\d{4})", nxt.get_text()) if nxt else None
                if mm:
                    job["valid_through"] = f"{mm.group(3)}-{mm.group(2)}-{mm.group(1)}"
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
    job["info_box"] = info

    # 3. Applicants count (capped at 50 by the site; may be missing)
    num = soup.select_one(".applicants-num")
    job["applicants"] = (int(num.get_text(strip=True))
                         if num and num.get_text(strip=True).isdigit() else None)

    # 4. Description from HTML: line breaks only at <br> and between <p>/<li> blocks
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
