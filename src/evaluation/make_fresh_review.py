"""Create the review sheet for the fresh test set: every row with its first labels and the text evidence.

    python -m src.evaluation.make_fresh_review

Writes data/gold/fresh_reviewed_sample_labeled.xlsx. Its label columns start as a copy of the first
labels; correct only what disagrees with the Legend and say why in review_note.

Rows are flagged (yellow cell + reason in 'check', flagged rows first) when a label does not match the
text evidence by the Legend's definitions, e.g. 'unspecified' seniority while the text asks for 2
years, or 'onsite' while the text has no work-mode word. Flags come from simple patterns taken from
the Legend, never from the extraction rules or predictions, so the review does not pull the labels
towards the rules. Tools are not flagged.

If the review file already exists, the reviewed cells are kept (the old file is saved as .bak).
The first labels stay untouched in fresh_sample_labeled.xlsx, so both versions can be scored:

    python -m src.models.train_classifiers --test-set fresh             as first labeled
    python -m src.models.train_classifiers --test-set fresh_reviewed    after review
"""
import re
import shutil

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from src.evaluation.make_fresh_sample import CHOICES, WORKMODE
from src.evaluation.make_test_sample import BOLD, BORDER, FILL, FONT
from src.evaluation.score_gold import labels_path

LABELS = ["tools", "remote", "seniority", "is_manager"]
EDITABLE = ["tools", "remote", "seniority", "is_manager", "review_note"]
# words that state a level by the Legend ('confirmé', 'expérimenté' alone do not)
LEVEL = (r"(?i)\b(senior|junior|d[ée]butant\w*|jeune\w* dipl[ôo]m\w*|stag(?:e|es|iaire)\w*|pfe|alternan\w*|"
         r"entry[- ]level|graduate)\b")
LEVEL_SHOWN = LEVEL[:-3] + r"|confirm[ée]\w*|exp[ée]riment[ée]\w*)\b"
YEARS = r"(?i)\b\d{1,2}\s*(?:\+\s*)?(?:ans?|ann[ée]es?|years?)\b"
MANAGEMENT = (r"(?i)\b(responsable|chef\w*|direct(?:eur|rice)\w*|superviseu\w*|manager|team lead|lead|head of|"
              r"g[ée]rant\w*|encadr\w*)\b")
COLUMNS = {"#": 5, "check": 30, "job_id": 11, "title": 34, "description": 90,
           "evidence_level": 42, "evidence_years": 42, "evidence_workmode": 42, "evidence_management": 30,
           "first_tools": 18, "first_remote": 11, "first_seniority": 11, "first_is_manager": 9,
           "tools": 22, "remote": 13, "seniority": 13, "is_manager": 11, "review_note": 36, "sample": 10}
YELLOW = PatternFill("solid", start_color="FFE699")
GREY = PatternFill("solid", start_color="F2F2F2")

INSTRUCTIONS = [
    ("Quick review", "Rows with a yellow cell come first: the reason is in 'check' and the cell to look at is "
                     "yellow. Read the evidence and, if needed, the description, then keep or change the label "
                     "(white columns) and write why in review_note."),
    ("What a flag means", "Only that the label and the text look inconsistent by the Legend: a level word or number of "
                          "years while seniority is 'unspecified', a work-mode word while remote is 'unspecified', a "
                          "management word in the title while is_manager is 'no', or the opposite. Many flags are "
                          "false alarms ('40 ans' of company history, 'interventions sur site', 'chef de projet'): "
                          "keeping the label is then the right answer."),
    ("Unflagged rows", "No inconsistency was found by these patterns, but they cannot see everything (a level written "
                       "in other words, tools). Look through them if time allows."),
    ("Afterwards", "Save this file under the same name, then score both versions: --test-set fresh (first labels) "
                   "and --test-set fresh_reviewed. Report both."),
]


def excerpts(text, pattern, width=45, limit=4):
    out, last_end = [], -1
    for m in re.finditer(pattern, text):
        if m.start() < last_end:
            continue
        start, end = max(0, m.start() - width), min(len(text), m.end() + width)
        out.append("…" + " ".join(text[start:end].split()) + "…")
        last_end = end
        if len(out) == limit:
            break
    return "\n".join(out)


def flags(r):
    """{label column: reason} where the first label and the text evidence disagree by the Legend."""
    text = f"{r.title}\n{r.description}"
    level, years = bool(re.search(LEVEL, text)), bool(re.search(YEARS, text))
    workmode, management = bool(re.search(WORKMODE, text)), bool(re.search(MANAGEMENT, r.title))
    seniority, remote, manager = (getattr(r, c).strip().lower() for c in ("seniority", "remote", "is_manager"))
    out = {}
    if seniority == "unspecified" and (level or years):
        out["seniority"] = "unspecified, but the text has " + ("a level word" if level else "a number of years")
    elif seniority not in ("", "unspecified") and not (level or years):
        out["seniority"] = f"{seniority}, but no level word or years found"
    if remote == "unspecified" and workmode:
        out["remote"] = "unspecified, but the text has a work-mode word"
    elif remote not in ("", "unspecified") and not workmode:
        out["remote"] = f"{remote}, but no work-mode word found"
    if manager == "no" and management:
        out["is_manager"] = "no, but the title has a management word"
    elif manager == "yes" and not management:
        out["is_manager"] = "yes, but no management word in the title"
    return out


def main():
    source, out = labels_path("fresh"), labels_path("fresh_reviewed")
    first = pd.read_excel(source, sheet_name="Labels", dtype=str).fillna("")
    legend = load_workbook(source)["Legend"]
    reviewed = {}
    if out.exists():
        old = pd.read_excel(out, sheet_name="Labels", dtype=str).fillna("")
        reviewed = old.set_index("job_id")[EDITABLE].to_dict("index")
        shutil.copy2(out, out.with_suffix(".bak.xlsx"))

    rows = []
    for r in first.itertuples():
        text = f"{r.title}\n{r.description}"
        found = flags(r)
        values = {"#": r._1, "check": "\n".join(f"{k}: {v}" for k, v in found.items()), "job_id": r.job_id,
                  "title": r.title, "description": r.description,
                  "evidence_level": excerpts(text, LEVEL_SHOWN), "evidence_years": excerpts(text, YEARS),
                  "evidence_workmode": excerpts(text, WORKMODE),
                  "evidence_management": excerpts(r.title, MANAGEMENT, width=25),
                  "review_note": r.review_note, "sample": r.sample}
        for label in LABELS:
            values[f"first_{label}"] = values[label] = getattr(r, label)
        values.update(reviewed.get(r.job_id, {}))
        rows.append((not found, r._1, values, found))
    rows.sort(key=lambda x: x[:2])                            # flagged rows first, then original order

    wb = Workbook()
    ws = wb.active
    ws.title = "Labels"
    names = list(COLUMNS)
    ws.append(names)
    for _, _, values, found in rows:
        ws.append([values[n] for n in names])
        row = ws.max_row
        for c, n in enumerate(names, 1):
            cell = ws.cell(row, c)
            cell.data_type = "s"                              # a leading '=' must stay text
            cell.font, cell.border = FONT, BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if n in found or (n == "check" and found):
                cell.fill = YELLOW
            elif n not in EDITABLE:
                cell.fill = GREY
        ws.row_dimensions[row].height = 200 if found else 60
    for c, n in enumerate(names, 1):
        letter = ws.cell(1, c).column_letter
        ws.column_dimensions[letter].width = COLUMNS[n]
        ws.column_dimensions[letter].hidden = n == "sample"
        if n in CHOICES:
            dv = DataValidation(type="list", formula1=f'"{CHOICES[n]}"', allow_blank=False, showErrorMessage=True)
            ws.add_data_validation(dv)
            dv.add(f"{letter}2:{letter}{ws.max_row}")
    for cell in ws[1]:
        cell.font, cell.fill = BOLD, FILL
        cell.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
    ws.freeze_panes = "D2"
    ws.auto_filter.ref = ws.dimensions

    wi = wb.create_sheet("Instructions")
    wl = wb.create_sheet("Legend")
    for sheet, lines in ((wi, INSTRUCTIONS), (wl, legend.iter_rows(values_only=True))):
        for line in lines:
            sheet.append(list(line))
        sheet.column_dimensions["A"].width, sheet.column_dimensions["B"].width = 26, 120
        for a, b in sheet.iter_rows():
            a.font, b.font = Font(name="Arial", size=10, bold=True), FONT
            a.alignment = b.alignment = Alignment(vertical="top", wrap_text=True)

    wb.save(out)
    counts = pd.Series([k for *_, found in rows for k in found]).value_counts().to_dict()
    flagged = sum(bool(found) for *_, found in rows)
    print(f"Saved {len(rows)} rows to {out}\nFlagged rows: {flagged} (first in the sheet) | flags per label: {counts}"
          + (f"\nKept {len(reviewed)} reviewed rows from the previous file (backup: .bak.xlsx)" if reviewed else ""))


if __name__ == "__main__":
    main()
