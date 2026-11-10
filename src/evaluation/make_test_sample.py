"""Build the HELD-OUT test set: 30 jobs that are NOT in the development sample.

    python -m src.evaluation.make_test_sample

Writes data/gold/test_sample_labeled.xlsx (Labels, Descriptions, Legend sheets).
You label the three columns by hand (tools, remote, seniority) WITHOUT looking at
any model output. Then run each extraction step ONCE on it and report that score.
Refuses to overwrite an existing file (your labeling work is safe); use --force to replace it.
"""
import argparse

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

import config
from src.evaluation.score_gold import labels_path, load_gold

N = 30
FONT = Font(name="Arial", size=10)
BOLD = Font(name="Arial", size=10, bold=True, color="FFFFFF")
FILL = PatternFill("solid", start_color="1F3864")
SIDE = Side(style="thin", color="D9D9D9")
BORDER = Border(left=SIDE, right=SIDE, top=SIDE, bottom=SIDE)

LEGEND = [
    ("Held-out test set", "Label these 30 jobs BEFORE looking at any model output. Use only what the text says."),
    ("tools", "Software, systems or equipment NAMED in the text. Empty if none. Generic phrases ('outils bureautiques', 'logiciel') and methods (5S, Lean) are not tools."),
    ("remote", "onsite / hybrid / remote / unspecified. 'unspecified' unless the text says it (télétravail, hybride, in-office...)."),
    ("seniority", "intern = stage/PFE/alternance; junior = débutant/jeune diplômé/under 2 years; mid = 2 to under 5 years, no senior/manager wording; senior = senior/confirmé or 5+ years; manager = responsable/chef/lead/directeur/superviseur (wins over senior); unspecified = no level word and no years."),
    ("Tip", "If a posting asks for both beginners and experienced people, label it unspecified."),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    out = labels_path("test")
    if out.exists() and not args.force:
        print(f"{out.name} already exists, not overwriting. Use --force to replace it.")
        return

    dev_ids = set(load_gold("gold")[0].job_id)
    df = pd.read_csv(config.JOBS_CLEAN_CSV, dtype={"job_id": str})
    ok = df[~df.is_duplicate & ~df.low_quality_desc & (df.desc_len >= 300) & ~df.job_id.isin(dev_ids)].copy()
    ok["primary_cat"] = ok.categories.fillna("").str.split("|").str[0]

    top = ok.primary_cat.value_counts().head(N).index
    parts = [g.sample(1, random_state=7) for _, g in ok[ok.primary_cat.isin(top)].groupby("primary_cat")]
    pick = pd.concat(parts)
    rest = ok.drop(pick.index)
    pick = pd.concat([pick, rest.sample(min(max(N - len(pick), 0), len(rest)), random_state=7)])
    pick = pick.head(N).sample(frac=1, random_state=3).reset_index(drop=True)
    assert not (set(pick.job_id) & dev_ids), "overlap with the development sample"

    wb = Workbook()
    ws = wb.active
    ws.title = "Labels"
    cols = ["#", "job_id", "title", "company", "category", "tools", "remote", "seniority", "review_note"]
    ws.append(cols)
    for i, r in enumerate(pick.itertuples(), 1):
        ws.append([i, r.job_id, r.title, r.company, r.primary_cat, "", "", "", ""])
    for c, w in enumerate([5, 11, 42, 26, 22, 38, 14, 14, 40], 1):
        ws.column_dimensions[get_column_letter(c)].width = w
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=len(cols)):
        for cell in row:
            cell.font, cell.border = FONT, BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for cell in ws[1]:
        cell.font, cell.fill = BOLD, FILL
        cell.alignment = Alignment(vertical="center", horizontal="center")
    ws.freeze_panes = "D2"
    dv_r = DataValidation(type="list", formula1='"onsite,hybrid,remote,unspecified"', allow_blank=True)
    dv_s = DataValidation(type="list", formula1='"intern,junior,mid,senior,manager,unspecified"', allow_blank=True)
    ws.add_data_validation(dv_r)
    ws.add_data_validation(dv_s)
    dv_r.add(f"G2:G{ws.max_row}")
    dv_s.add(f"H2:H{ws.max_row}")

    wd = wb.create_sheet("Descriptions")
    wd.append(["#", "job_id", "title", "description"])
    for i, r in enumerate(pick.itertuples(), 1):
        wd.append([i, r.job_id, r.title, "\n".join(l.strip() for l in str(r.description).splitlines() if l.strip())])
    for c, w in zip("ABCD", [5, 11, 36, 150]):
        wd.column_dimensions[c].width = w
    for row in wd.iter_rows(min_row=2, max_row=wd.max_row, max_col=4):
        for cell in row:
            cell.font, cell.border = Font(name="Arial", size=9), BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for cell in wd[1]:
        cell.font, cell.fill = BOLD, FILL
    wd.freeze_panes = "A2"

    wl = wb.create_sheet("Legend")
    for row in LEGEND:
        wl.append(list(row))
    wl.column_dimensions["A"].width = 20
    wl.column_dimensions["B"].width = 120
    for row in wl.iter_rows(min_row=1, max_row=wl.max_row, max_col=2):
        for cell in row:
            cell.font = FONT
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for r in range(1, wl.max_row + 1):
        wl.cell(r, 1).font = Font(name="Arial", size=10, bold=True)

    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    print(f"Saved {len(pick)} jobs to {out}")
    print("Categories:", pick.primary_cat.value_counts().to_dict())


if __name__ == "__main__":
    main()
