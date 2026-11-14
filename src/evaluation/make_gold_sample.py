"""Create a development sample for manual labeling."""
import argparse

import pandas as pd

import config


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if config.GOLD_SAMPLE_CSV.exists() and not args.force:
        print("gold_sample.csv already exists, not overwriting. Use --force to replace it.")
        return

    df = pd.read_csv(config.JOBS_CLEAN_CSV, dtype={"job_id": str})
    ok = df[~df.is_duplicate & ~df.low_quality_desc & (df.desc_len >= 300)].copy()
    ok["primary_cat"] = ok.categories.fillna("").str.split("|").str[0]

    top = ok.primary_cat.value_counts().head(20).index
    parts = [g.sample(min(2, len(g)), random_state=42)
             for _, g in ok[ok.primary_cat.isin(top)].groupby("primary_cat")]
    gold = pd.concat(parts)
    rest = ok.drop(gold.index)
    gold = pd.concat([gold, rest.sample(min(max(50 - len(gold), 0), len(rest)), random_state=42)])
    gold = gold.sample(frac=1, random_state=1)

    gold = gold[["job_id", "title", "company", "primary_cat", "description"]]
    for col in ["tools", "remote", "seniority"]:
        gold[col] = ""
    config.GOLD_DIR.mkdir(parents=True, exist_ok=True)
    gold.to_csv(config.GOLD_SAMPLE_CSV, index=False, encoding="utf-8-sig")
    print("Saved", len(gold), "jobs to", config.GOLD_SAMPLE_CSV)
    print(gold.primary_cat.value_counts().to_dict())


if __name__ == "__main__":
    main()
