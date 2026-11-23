"""Create the fresh test set: jobs never labeled before, to be labeled without seeing predictions.

    python -m src.evaluation.make_fresh_sample

150 jobs drawn at random (the headline score) plus 50 drawn at random among the postings that
contain a work-mode word (so that remote / hybrid / onsite have enough examples). The two groups are
shuffled together; the hidden 'sample' column tells them apart when scoring. Reposts of an already
labeled job (same company and title) are left out. Existing files are never overwritten.
"""
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.worksheet.datavalidation import DataValidation

import config
from src.evaluation.make_test_sample import BOLD, BORDER, FILL, FONT
from src.evaluation.score_gold import labels_path, load_gold

N_RANDOM, N_WORKMODE, SEED = 150, 50, 2026
WORKMODE = (r"(?i)t[ée]l[ée]-?travail|\bremote\b|hybrid|pr[ée]sentiel|sur site|on-?site|[àa] distance|"
            r"in[- ]office|work from home")
COLUMNS = {"#": 5, "job_id": 11, "title": 42, "company": 24, "category": 20, "description": 110, "tools": 34,
           "remote": 14, "seniority": 14, "is_manager": 12, "review_note": 34, "sample": 10}
CHOICES = {"remote": "onsite,hybrid,remote,unspecified",
           "seniority": "intern,junior,mid,senior,unspecified",
           "is_manager": "yes,no"}

LEGEND = [
    ("How to label", "Use only what the title and description say. Do not open predictions, the website or the "
                     "database while labeling. Fill remote, seniority and is_manager on EVERY row (dropdown lists)."),
    ("tools", "Named software, platforms, programming languages, databases, operating systems. Separate with commas: "
              "Excel, Power BI. Leave empty if none. NOT tools: generic words (ERP, CRM, outils bureautiques, IA), "
              "methods (5S, HACCP, Agile, UML), machines, skills. Facebook / Instagram / WhatsApp count only when "
              "the job uses them as a work tool, not as a contact channel."),
    ("remote = onsite", "Only when the text STATES that the work is done on the premises: 'en présentiel', 'poste basé "
                        "sur site', 'travail sur site', 'in-office'. NOT onsite: an address or city alone, 'déplacements "
                        "sur site', 'interventions / travaux sur site', 'chez le client'."),
    ("remote = remote", "Work fully from a distance: 'télétravail', '100% remote', 'poste à distance'. NOT remote: "
                        "'assistance à distance', 'formation à distance', 'occasional remote day' (that is hybrid)."),
    ("remote = hybrid", "Mix of both, or a choice between them: 'hybride', '2 jours de télétravail', 'à distance ou sur "
                        "site'. NOT hybrid: 'véhicule hybride', 'poste hybride' meaning versatile, hybrid cloud."),
    ("remote = unspecified", "Everything else. This is the normal case."),
    ("seniority = intern", "stage, stagiaire, PFE, alternance."),
    ("seniority = junior", "junior, débutant, jeune diplômé, or under 2 years of experience asked."),
    ("seniority = mid", "2 to under 5 years asked."),
    ("seniority = senior", "the word senior, or 5 years or more asked."),
    ("seniority = unspecified", "No level word and no number of years. Also when both beginners and experienced people "
                                "are welcome. 'confirmé', 'expérimenté', 'expérience exigée' alone are NOT a level."),
    ("range of years", "Use the lower number: '2 à 5 ans' = mid."),
    ("is_manager", "yes when the person manages a team or heads a department (responsable, chef, directeur, "
                   "superviseur, team lead). no for job names that only sound like it: community manager, account "
                   "manager, product manager, chef de projet, chef de partie, responsable d'un poste without a team. "
                   "Independent of seniority: a manager also gets a seniority value."),
    ("Not sure", "Choose the best answer and write why in review_note."),
]


def pick():
    df = pd.read_csv(config.JOBS_CLEAN_CSV, dtype={"job_id": str})
    df["key"] = (df["company_key"].fillna("") + "|"
                 + df["title"].fillna("").str.lower().str.replace(r"\W+", " ", regex=True).str.strip())
    labeled = set()
    for prefix in ("gold", "expanded", "test"):
        labeled |= set(load_gold(prefix)[0]["job_id"])
    labeled_keys = set(df.loc[df["job_id"].isin(labeled), "key"])
    pool = df[~df["is_duplicate"] & ~df["low_quality_desc"] & ~df["key"].isin(labeled_keys)]
    pool = pool.sample(frac=1, random_state=SEED).drop_duplicates("key")
    random_part = pool.head(N_RANDOM)
    rest = pool.iloc[N_RANDOM:]
    text = rest["title"].fillna("") + "\n" + rest["description"].fillna("")
    workmode_part = rest[text.str.contains(WORKMODE, regex=True)].head(N_WORKMODE)
    picked = pd.concat([random_part.assign(sample="random"), workmode_part.assign(sample="workmode")])
    return picked.sample(frac=1, random_state=SEED).reset_index(drop=True)


def main():
    out = labels_path("fresh")
    if out.exists():
        raise SystemExit(f"{out} already exists: not overwritten, it may contain hand labels.")
    picked = pick()
    picked["category"] = picked["categories"].fillna("").str.split("|").str[0]

    wb = Workbook()
    ws = wb.active
    ws.title = "Labels"
    names = list(COLUMNS)
    ws.append(names)
    for i, r in enumerate(picked.itertuples(), 1):
        description = "\n".join(line.strip() for line in str(r.description).splitlines() if line.strip())
        values = {"#": i, "job_id": r.job_id, "title": r.title, "company": r.company, "category": r.category,
                  "description": description, "sample": r.sample}
        ws.append([values.get(n, "") for n in names])
        for n in ("title", "company", "description"):        # a leading '=' must stay text
            ws.cell(i + 1, names.index(n) + 1).data_type = "s"
        ws.row_dimensions[i + 1].height = 240
    for c, n in enumerate(names, 1):
        letter = ws.cell(1, c).column_letter
        ws.column_dimensions[letter].width = COLUMNS[n]
        ws.column_dimensions[letter].hidden = n == "sample"
        if n in CHOICES:
            dv = DataValidation(type="list", formula1=f'"{CHOICES[n]}"', allow_blank=True, showErrorMessage=True)
            ws.add_data_validation(dv)
            dv.add(f"{letter}2:{letter}{ws.max_row}")
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.font, cell.border = FONT, BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for cell in ws[1]:
        cell.font, cell.fill = BOLD, FILL
        cell.alignment = Alignment(vertical="center", horizontal="center")
    ws.freeze_panes = "D2"

    wl = wb.create_sheet("Legend")
    for row in LEGEND:
        wl.append(row)
    wl.column_dimensions["A"].width, wl.column_dimensions["B"].width = 26, 120
    for a, b in wl.iter_rows():
        a.font, b.font = Font(name="Arial", size=10, bold=True), FONT
        a.alignment = b.alignment = Alignment(vertical="top", wrap_text=True)

    wb.save(out)
    print(f"Saved {len(picked)} jobs to {out}: {picked['sample'].value_counts().to_dict()}")


if __name__ == "__main__":
    main()
