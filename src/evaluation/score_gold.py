"""Score the extraction against the hand-labeled sample.

    python -m src.evaluation.score_gold

Reads the reviewed labels (data/gold/gold_sample_labeled.xlsx if present,
otherwise gold_labels.csv) and data/extracted/gold_predictions.csv.
"""
import argparse
import io

import pandas as pd

import config
from src.extraction.rules import INTERN_TITLE, MANAGER_TITLE, NOT_MANAGER, SENIOR_STRONG, fold


def labels_path(prefix):
    return config.GOLD_DIR / f"{prefix}_sample_labeled.xlsx"


def predictions_path(prefix):
    return config.EXTRACTED_DIR / f"{prefix}_predictions.csv"


def read_labels_csv(path):
    """Read a labels CSV exported from Excel (any of ; , tab as separator, UTF-8 or Windows-1252)."""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    header = text.splitlines()[0] if text else ""
    sep = max([",", ";", "\t"], key=header.count)      # the separator most used in the header line
    return pd.read_csv(io.StringIO(text), sep=sep, dtype=str)


def load_gold(prefix="gold"):
    """Hand labels of one set: 'gold' = development sample, 'test' = held-out sample.

    Accepts <prefix>_sample_labeled.xlsx and/or <prefix>_sample_labeled.csv (the CSV you
    export from Excel Online). If both exist, the most recently saved one is used."""
    xlsx = labels_path(prefix)
    csv_path = xlsx.with_suffix(".csv")
    existing = [p for p in (xlsx, csv_path) if p.exists()]
    if existing:
        path = max(existing, key=lambda p: p.stat().st_mtime)
    elif prefix == "gold" and config.GOLD_LABELS_CSV.exists():
        path = config.GOLD_LABELS_CSV
    else:
        raise SystemExit(f"No labels found. Expected {xlsx.name} or {csv_path.name} in {config.GOLD_DIR}")
    if path.suffix.lower() == ".xlsx":
        df = pd.read_excel(path, sheet_name="Labels", dtype=str)
    else:
        df = read_labels_csv(path)
    df.columns = [str(c).strip().lower() for c in df.columns]
    df = df.fillna("")
    for col in ["job_id", "tools", "remote", "seniority"]:
        if col not in df.columns:
            raise SystemExit(f"Column '{col}' missing in {path.name}. Columns found: {list(df.columns)}. "
                             f"If you exported a CSV, make sure the 'Labels' sheet was the active one.")
    df["job_id"] = df["job_id"].str.strip()
    df = df[df.job_id != ""].copy()                       # drop empty rows Excel sometimes adds
    df["seniority"] = df["seniority"].str.strip().str.lower()
    df["remote"] = df["remote"].str.strip().str.lower()
    return df, path.name


VALID_REMOTE = {"onsite", "hybrid", "remote", "unspecified"}
VALID_SENIORITY = {"intern", "junior", "mid", "senior", "manager", "unspecified"}
LABEL_WORDS = VALID_REMOTE | VALID_SENIORITY | {"none", "n/a", "na", "-", "aucun", "aucune", "nan"}


def clean_tools_cell(cell):
    """A tools cell must hold tool names. Label words typed by mistake ('unspecified', 'none')
    mean 'no tool named' and are treated as empty."""
    items = [x.strip() for x in str(cell).replace("|", ",").replace(";", ",").split(",")]
    return ", ".join(x for x in items if x and x.lower() not in LABEL_WORDS)


def check_labels(df):
    """Print problems found in the hand labels. Returns False if scoring must stop."""
    print("=== LABEL CHECK ===")
    ok = True
    bad_sen = df[~df.seniority.isin(VALID_SENIORITY)]
    bad_rem = df[~df.remote.isin(VALID_REMOTE)]
    if len(bad_sen) or len(bad_rem):
        ok = False
        print(f"ERROR: {len(bad_sen)} invalid seniority and {len(bad_rem)} invalid remote values "
              f"(empty or misspelled). Use the dropdown lists. First ones:")
        for r in pd.concat([bad_sen, bad_rem]).drop_duplicates("job_id").head(8).itertuples():
            print(f"  {r.job_id} | seniority={r.seniority!r} remote={r.remote!r} | {r.title[:45]}")
    typed = (df.tools_raw.map(lambda c: str(c).strip().lower()).isin(LABEL_WORDS)
             & (df.tools_raw.str.strip() != "")).sum()
    if typed:
        print(f"NOTE: {typed} tools cells contained a label word ('unspecified', 'none'...). "
              f"They were treated as EMPTY (= no tool named). Leave the cell empty next time.")
    share = (df.remote != "unspecified").mean()
    if share > 0.5:
        print(f"WARNING: {share * 100:.0f}% of jobs are labeled with a remote value other than 'unspecified'. "
              f"Use 'onsite' only when the text says so (sur site, presentiel, in-office); otherwise 'unspecified'.")
    warn = []
    for r in df.itertuples():
        t = fold(r.title)
        if MANAGER_TITLE.search(t) and not NOT_MANAGER.search(t) and r.seniority != "manager":
            warn.append((r.job_id, r.seniority, "title has responsable/chef/lead/manager -> manager?", r.title))
        elif SENIOR_STRONG.search(t) and r.seniority != "senior":
            warn.append((r.job_id, r.seniority, "title says senior/confirme -> senior?", r.title))
        elif INTERN_TITLE.search(t) and r.seniority != "intern":
            warn.append((r.job_id, r.seniority, "title says stage/PFE/alternance -> intern?", r.title))
    if warn:
        print(f"WARNING: {len(warn)} labels contradict the Legend (title keywords). Check them, or tell me "
              f"if you want a different definition:")
        for jid, lab, why, title in warn[:12]:
            print(f"  {jid} | labeled {lab:<11} | {why} | {title[:40]}")
    if ok and not typed and share <= 0.5 and not warn:
        print("No problems found.")
    print()
    return ok


def to_set(s):
    return {x.strip().lower() for x in str(s).replace("|", ",").split(",") if x.strip()}


def same_tool(a, b):
    """Lenient match: equal, or one contains the other ('excel' vs 'microsoft excel')."""
    return a == b or a in b or b in a


def tool_scores(df, col):
    """Lenient precision / recall of one predicted-tools column against the gold tools."""
    tp = fp = fn = 0
    bad = []
    for r in df.itertuples():
        g, p = to_set(r.tools), to_set(getattr(r, col))
        m_g = {x for x in g if any(same_tool(x, y) for y in p)}
        m_p = {y for y in p if any(same_tool(x, y) for x in g)}
        tp += len(m_g)
        fn += len(g - m_g)
        fp += len(p - m_p)
        if (g - m_g) or (p - m_p):
            bad.append((r.job_id, sorted(g - m_g), sorted(p - m_p)))
    prec = tp / (tp + fp) if tp + fp else 0
    rec = tp / (tp + fn) if tp + fn else 0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0
    return prec, rec, f1, tp, fp, fn, bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="gold", choices=["gold", "test"],
                    help="gold = development sample, test = held-out sample")
    args = ap.parse_args()
    gold, src = load_gold(args.set)
    gold["tools_raw"] = gold["tools"]
    gold["tools"] = gold["tools"].map(clean_tools_cell)
    print(f"Labels file: {src}\n")
    if not check_labels(gold):
        raise SystemExit("Fix the labels above, save the file again, then rerun.")
    pred = pd.read_csv(predictions_path(args.set), dtype=str).fillna("")
    df = gold.merge(pred, on="job_id", suffixes=("", "_p"))
    n = len(df)
    print(f"Set: {args.set} | labels: {src} | predictions: {predictions_path(args.set).name} | jobs scored: {n}")
    if n == 0:
        return

    # ---- seniority ----
    print("\n=== SENIORITY ===")
    base = (df.seniority == "unspecified").mean()
    print(f"baseline 'always unspecified': {base * 100:.1f}%")
    for label, col in [("LLM only", "seniority_llm"), ("Rules only", "seniority_rules"),
                       ("Hybrid (final)", "seniority_pred")]:
        if col in df.columns:
            print(f"  {label:<15} accuracy {(df.seniority == df[col]).mean() * 100:.1f}%")
    print("\nConfusion matrix of the final (hybrid) result:")
    print(pd.crosstab(df.seniority.rename("gold"), df.seniority_pred.rename("predicted")).to_string())
    wrong = df[df.seniority != df.seniority_pred]
    if len(wrong):
        print(f"\nDisagreements ({len(wrong)}), check these first:")
        for r in wrong.head(15).itertuples():
            extra = (f" (llm={r.seniority_llm}, rules={r.seniority_rules})"
                     if "seniority_llm" in df.columns else "")
            print(f"  {r.job_id} | gold={r.seniority:<11} pred={r.seniority_pred:<11}{extra} | {r.title[:45]}")

    # ---- tools ----
    print("\n=== TOOLS (lenient match) ===")
    final_bad = []
    for label, col in [("LLM only", "tools_llm"), ("Dictionary only", "tools_rules"),
                       ("Hybrid (final)", "tools_pred")]:
        if col in df.columns:
            prec, rec, f1, tp, fp, fn, bad = tool_scores(df, col)
            print(f"  {label:<16} precision {prec * 100:5.1f}% | recall {rec * 100:5.1f}% | F1 {f1 * 100:5.1f}%  "
                  f"(TP {tp}, FP {fp}, missed {fn})")
            if col == "tools_pred":
                final_bad = bad
    for jid, missed, extra in final_bad[:12]:
        print(f"  final | {jid} | missed: {missed or '-'} | extra: {extra or '-'}")

    # ---- remote ----
    racc = (df.remote == df.remote_pred).mean()
    print("\n=== REMOTE (keyword rules) ===")
    print(f"Accuracy: {racc * 100:.1f}% | gold distribution: {df.remote.value_counts().to_dict()}")
    for r in df[df.remote != df.remote_pred].itertuples():
        print(f"  {r.job_id} | gold={r.remote} pred={r.remote_pred} | {r.title[:55]}")


if __name__ == "__main__":
    main()