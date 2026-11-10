import pandas as pd

df = pd.read_csv("jobs_clean.csv", dtype={"job_id": str})
ok = df[~df.is_duplicate & ~df.low_quality_desc & (df.desc_len >= 300)].copy()
ok["primary_cat"] = ok.categories.fillna("").str.split("|").str[0]

top = ok.primary_cat.value_counts().head(20).index
parts = [g.sample(min(2, len(g)), random_state=42)
         for _, g in ok[ok.primary_cat.isin(top)].groupby("primary_cat")]
gold = pd.concat(parts)
rest = ok.drop(gold.index).sample(50 - len(gold), random_state=42)
gold = pd.concat([gold, rest]).sample(frac=1, random_state=1)

gold = gold[["job_id", "title", "company", "primary_cat", "description"]]
for col in ["tools", "remote", "seniority"]:
    gold[col] = ""
gold.to_csv("gold_sample.csv", index=False, encoding="utf-8-sig")
print("Saved", len(gold), "jobs to gold_sample.csv")
print(gold.primary_cat.value_counts().to_dict())