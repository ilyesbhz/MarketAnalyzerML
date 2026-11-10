import glob
import gzip
import json
import os
import re
from collections import Counter

import pandas as pd
from parse_job import parse_job

RAW_DIR = "raw"

# URL + sitemap lastmod by job id
with open("job_urls.json", encoding="utf-8") as f:
    meta = {u["url"].split("/job/")[1].split("/")[0]: u for u in json.load(f)}


def read_raw(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return f.read()
    with open(path, encoding="utf-8") as f:
        return f.read()


def split_list(s, sep=","):
    return [x.strip() for x in s.split(sep) if x.strip()] if s else []


def norm(s):
    return " ".join(s.replace("\xa0", " ").split()) if s else s

def clean_company(s):
    s = norm(s)
    if not s:
        return s
    s = re.sub(r"\s*-\s*Entreprise\s+V[ée]rifi[ée]e?\s+par\s+Tanitjobs\.com\s*$", "", s, flags=re.I)
    return s.strip(" -")

def experience_min(s):
    m = re.search(r"(\d+)", s or "")
    return int(m.group(1)) if m else None


def education_level(s):
    if not s:
        return None
    low = s.lower()
    nums = [int(n) for n in re.findall(r"Bac\s*\+\s*(\d)", s)]
    if nums:
        return max(nums)
    if "doctorat" in low:
        return 8
    if "ingénieur" in low or "ingenieur" in low:
        return 5
    if any(k in low for k in ["bac professionnel", "bep", "lycée", "niveau bac", "non validé"]):
        return 0  # Bac or below
    return None  # "Etudiant", "Médecin ...": left missing on purpose

def experience_years(s):
    if not s:
        return None
    if "débutant" in s.lower():
        return 0
    return experience_min(s)


def salary(s):
    """Return (min, max, kind). kind: bracket / confidential / none / other."""
    if not s:
        return None, None, "none"
    low = s.lower()
    if "confidentiel" in low:
        return None, None, "confidential"
    nums = [int(n) for n in re.findall(r"\d+", s.replace(" ", ""))]
    if low.startswith("inférieur") and nums:
        return 0, nums[0], "bracket"
    if low.startswith("plus") and nums:
        return nums[0], None, "bracket"
    if len(nums) >= 2:
        return nums[0], nums[1], "bracket"
    return None, None, "other"



rows = []
paths = glob.glob(f"{RAW_DIR}/*.html.gz") + glob.glob(f"{RAW_DIR}/*.html")
seen = set()
for p in sorted(paths):
    jid = os.path.basename(p).split(".")[0]
    if jid in seen:
        continue
    seen.add(jid)
    url = meta.get(jid, {}).get("url")
    try:
        j = parse_job(read_raw(p), url=url)
    except Exception as e:
        print("parse error", jid, e)
        continue
    ib = j["info_box"]
    smin, smax, skind = salary(ib.get("Rémunération proposée"))
    desc = norm_desc = (j["description"] or "").replace("\xa0", " ")
    rows.append({
        "job_id": jid,
        "url": url,
        "title": norm(j["title"]),
        "company": clean_company(j["company"]),
        "city": norm(j["city"]),
        "region": norm(j["region"]) or norm(j["city"]),
        "categories": "|".join(split_list(j["category"])),
        "date_posted": (j["date_posted"][:10] if j["date_posted"]
                        else (meta.get(jid, {}).get("lastmod") or None)),
        "date_source": "jsonld" if j["date_posted"] else "sitemap_lastmod",
        "parsed_from": j["parsed_from"],
        "valid_through": j["valid_through"][:10] if j["valid_through"] else None,
        "employment_types": "|".join(j["employment_types"]),
        "vacancies": experience_min(ib.get("Postes vacants")),
        "experience_raw": ib.get("Experience"),
        "experience_min_years": experience_years(ib.get("Experience")),
        "education_raw": ib.get("Niveau d'étude"),
        "education_level": education_level(ib.get("Niveau d'étude")),
        "languages": "|".join(split_list(ib.get("Langue"))),
        "salary_raw": ib.get("Rémunération proposée"),
        "salary_min": smin,
        "salary_max": smax,
        "salary_kind": skind,
        "applicants": j["applicants"],
        "description": desc,
        "desc_len": len(desc),
        "low_quality_desc": len(desc) < 100,
    })

df = pd.DataFrame(rows)
df["company_key"] = df.company.str.lower().str.strip()
df["is_anonymous"] = df.company_key.str.match(r"^(anonym|confidentiel)", na=False)
df["dup_key"] = (df["title"].str.lower().fillna("") + "||" +
                 df["company"].str.lower().fillna("") + "||" +
                 df["description"].str[:300])
df["is_duplicate"] = df.duplicated("dup_key", keep="first")
df["is_expired"] = pd.to_datetime(df.valid_through) < pd.Timestamp.today().normalize()
df = df.drop(columns="dup_key")

df.to_csv("jobs_clean.csv", index=False, encoding="utf-8-sig")
df.to_json("jobs_clean.json", orient="records", force_ascii=False, indent=1)

# ---------------- REPORT ----------------
print("\n=== REPORT ===")
print("Rows:", len(df), "| duplicates:", int(df.is_duplicate.sum()),
      "| low-quality descriptions:", int(df.low_quality_desc.sum()))
print("\nMissing values (%):")
print((df.isna().mean() * 100).round(1).sort_values(ascending=False).head(12).to_string())
print("\nDate posted range:", df.date_posted.min(), "->", df.date_posted.max())
print("\nTop regions:\n", df.region.value_counts().head(8).to_string())
cats = Counter(c for s in df.categories for c in s.split("|") if c)
print("\nTop categories:", cats.most_common(8))
print("\nExperience values:", df.experience_raw.value_counts(dropna=False).to_dict())
print("\nEducation parsed -> level counts:", df.education_level.value_counts(dropna=False).to_dict())
unk = df[df.education_raw.notna() & df.education_level.isna()].education_raw.value_counts().head(8)
print("Education values NOT mapped:", unk.to_dict())
print("\nSalary kinds:", df.salary_kind.value_counts().to_dict())
print("Salary raw (distinct brackets):", df[df.salary_kind.isin(['bracket', 'other'])].salary_raw.value_counts().to_dict())
print("\nLanguages:", Counter(l for s in df.languages for l in s.split("|") if l).most_common())
print("\nApplicants describe:\n", df.applicants.describe().round(1).to_string())
print("\nParsed from:", df.parsed_from.value_counts().to_dict())
print("Still missing title / company / region:",
      int(df.title.isna().sum()), int(df.company.isna().sum()), int(df.region.isna().sum()))
print(df[df.parsed_from == "html"][["job_id", "title", "company", "region", "categories",
                                    "date_posted", "valid_through"]].head(8).to_string())
print("\nExpired already:", int(df.is_expired.sum()),
      "| usable (not duplicate, not low-quality):",
      int((~df.is_duplicate & ~df.low_quality_desc).sum()))
print("Distinct companies:", df.company.nunique())
print("Top companies:", df.company.value_counts().head(8).to_dict())
it = df[df.categories.str.contains("Informatique|Technologie de l'information", na=False)]
print("\nIT jobs:", len(it), "| with salary bracket:", int((it.salary_kind == 'bracket').sum()))
kw = df.title.str.contains(r"data|bi\b|business intelligence|analyst|power bi|sql|etl", case=False, na=False)
print("Titles matching data/BI keywords:", int(kw.sum()))
print(df[kw][["title", "company", "region"]].head(10).to_string())
print("\nAnonymous postings:", int(df.is_anonymous.sum()),
      "| distinct real companies:", df[~df.is_anonymous].company_key.nunique())