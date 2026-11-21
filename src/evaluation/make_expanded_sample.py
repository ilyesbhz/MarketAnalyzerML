"""Create a larger, recent, and representative sample for manual labeling."""
import argparse
import re

import pandas as pd

import config
from src.evaluation.score_gold import load_gold

OUT = config.GOLD_DIR / "expanded_sample_labeled.xlsx"
REMOTE_WORDS = re.compile(r"remote|t[ée]l[ée]travail|hybride|hybrid|pr[ée]sentiel|sur site|in[- ]office", re.I)
LEVEL_WORDS = re.compile(
    r"stage|stagiaire|pfe|alternance|junior|d[ée]butant|senior|confirm[ée]|"
    r"responsable|chef|lead|directeur|manager|\d+\s*(?:ans?|ann[ée]es?|years?)",
    re.I,
)
TOOL_WORDS = re.compile(
    r"\b(excel|sap|python|java|sql|postgresql|mysql|docker|autocad|photoshop|"
    r"power\s*bi|salesforce|odoo|aws|azure|linux|jira)\b", re.I,
)


def existing_ids():
    ids = set(load_gold("gold")[0].job_id)
    try:
        ids.update(load_gold("test")[0].job_id)
    except SystemExit:
        pass
    return ids


def pick_candidates(df, size):
    eligible = df[
        ~df.is_duplicate & ~df.low_quality_desc & (df.desc_len >= 300)
        & ~df.job_id.isin(existing_ids())
    ].copy()
    eligible["primary_cat"] = eligible.categories.fillna("").str.split("|").str[0]
    eligible["date_posted"] = pd.to_datetime(eligible.date_posted, errors="coerce")
    eligible["text"] = eligible.title.fillna("") + "\n" + eligible.description.fillna("")
    eligible["priority"] = (
        eligible.text.str.contains(REMOTE_WORDS).astype(int) * 3
        + eligible.text.str.contains(LEVEL_WORDS).astype(int) * 2
        + eligible.text.str.contains(TOOL_WORDS).astype(int)
    )
    eligible = eligible.sort_values(
        ["priority", "date_posted"], ascending=[False, False], kind="stable"
    )

    selected = []
    top_categories = eligible.primary_cat.value_counts().head(25).index
    for category in top_categories:
        group = eligible[eligible.primary_cat == category]
        if len(group):
            selected.append(group.head(1))

    recent_count = min(max(size // 2, 1), len(eligible))
    selected.append(eligible.sort_values("date_posted", ascending=False).head(recent_count))
    selected.append(eligible.head(size))
    picked = pd.concat(selected).drop_duplicates("job_id").head(size)
    if len(picked) < size:
        remaining = eligible[~eligible.job_id.isin(picked.job_id)]
        picked = pd.concat([picked, remaining.head(size - len(picked))])
    return picked.sort_values("date_posted", ascending=False).head(size)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=200)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if OUT.exists() and not args.force:
        print(f"{OUT.name} already exists; use --force to replace it.")
        return

    jobs = pd.read_csv(config.JOBS_CLEAN_CSV, dtype={"job_id": str})
    picked = pick_candidates(jobs, args.size)
    old, _ = load_gold("gold")
    old = old.assign(review_note="")
    old["category"] = old.get("category", "")
    old = old[["job_id", "title", "company", "category", "tools", "remote",
               "seniority", "review_note"]]

    new = picked.rename(columns={"primary_cat": "category"})[
        ["job_id", "title", "company", "category"]
    ].copy()
    for column in ["tools", "remote", "seniority", "review_note"]:
        new[column] = ""
    labels = pd.concat([old, new], ignore_index=True)
    labels.insert(0, "#", range(1, len(labels) + 1))

    descriptions = pd.concat([
        old[["job_id", "title"]].merge(
            jobs[["job_id", "description"]], on="job_id", how="left"
        ),
        picked[["job_id", "title", "description"]],
    ]).drop_duplicates("job_id")
    descriptions.insert(0, "#", range(1, len(descriptions) + 1))

    with pd.ExcelWriter(OUT, engine="openpyxl") as writer:
        labels.to_excel(writer, sheet_name="Labels", index=False)
        descriptions.to_excel(writer, sheet_name="Descriptions", index=False)
        pd.DataFrame({
            "field": ["tools", "remote", "seniority", "important"],
            "definition": [
                "Named software, platforms, programming languages, databases, or operating systems.",
                "remote / hybrid / onsite / unspecified; use unspecified unless the text explicitly says otherwise.",
                "intern, junior, mid, senior, manager, or unspecified; use explicit words or years only.",
                "Label the new rows without looking at model predictions. Keep the existing test set separate.",
            ],
        }).to_excel(writer, sheet_name="Legend", index=False)
    print(f"Saved {len(picked)} new candidates and {len(old)} existing labels to {OUT}")


if __name__ == "__main__":
    main()
