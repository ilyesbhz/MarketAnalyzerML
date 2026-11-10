"""Summarize every saved Adzuna raw file (no API calls).

    python -m src.collection.adzuna_inspect
"""
import gzip
import json

import pandas as pd

import config


def main():
    files = sorted(config.ADZUNA_RAW_DIR.rglob("*.json.gz"))
    if not files:
        print("No Adzuna files yet. Run the collector first.")
        return

    rows = []
    for p in files:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            rec = json.load(f)
        for j in rec["results"]:
            rows.append({
                "day": p.parent.name,
                "country": rec["country"],
                "query_category": rec["category"],
                "id": j.get("id"),
                "title": j.get("title"),
                "company": (j.get("company") or {}).get("display_name"),
                "created": j.get("created"),
                "contract_type": j.get("contract_type"),
                "contract_time": j.get("contract_time"),
                "salary_min": j.get("salary_min"),
                "salary_max": j.get("salary_max"),
                "predicted": str(j.get("salary_is_predicted")).lower() in ("1", "true"),
                "returned_category": (j.get("category") or {}).get("tag"),
                "desc_len": len(j.get("description") or ""),
            })
    df = pd.DataFrame(rows)
    df["created"] = pd.to_datetime(df["created"], errors="coerce", utc=True)
    has_sal = df.salary_min.notna() | df.salary_max.notna()

    print("=== ADZUNA RAW SUMMARY ===")
    print(f"Files: {len(files)} | days: {df.day.nunique()} | rows: {len(df)} | "
          f"distinct job ids: {df.id.nunique()}")
    print("Rows per country:", df.country.value_counts().to_dict())
    print("Rows per query category (top 8):", df.query_category.value_counts().head(8).to_dict())
    mism = (df.query_category != df.returned_category).mean() * 100
    print(f"Returned category differs from the queried one: {mism:.1f}%")
    print("Returned categories (top 8):", df.returned_category.value_counts().head(8).to_dict())

    print("\n--- salary ---")
    for c, g in df.groupby("country"):
        s = has_sal[g.index]
        real = (s & ~g.predicted).sum()
        pred = (s & g.predicted).sum()
        print(f"{c}: with a salary value {s.mean() * 100:.1f}% | real stated {real} "
              f"({real / len(g) * 100:.1f}%) | predicted {pred} ({pred / len(g) * 100:.1f}%)")
    same = (df.salary_min == df.salary_max) & has_sal
    print(f"Salary min == max (single value): {same.sum()} of {has_sal.sum()} with salary")

    print("\n--- dates ---")
    print("created range:", df.created.min(), "->", df.created.max())
    age = (pd.Timestamp.now(tz="UTC") - df.created).dt.days
    print("age in days (median / 90th percentile / max):",
          age.median(), age.quantile(0.9), age.max())

    print("\n--- text and other fields ---")
    print("description length: min", df.desc_len.min(), "| median", int(df.desc_len.median()),
          "| max", df.desc_len.max(), "| share >= 495 chars:",
          f"{(df.desc_len >= 495).mean() * 100:.1f}%")
    print("contract_type:", df.contract_type.value_counts(dropna=False).to_dict())
    print("contract_time:", df.contract_time.value_counts(dropna=False).to_dict())
    print(f"company missing: {df.company.isna().mean() * 100:.1f}%")
    dup = df.id.duplicated(keep=False)
    print(f"Rows whose job id appears more than once: {dup.sum()}")


if __name__ == "__main__":
    main()