"""Train and evaluate classical text classifiers on the labeled job samples.

Seniority and remote are learned (TF-IDF + linear models). Tools are NOT learned: with
~120 distinct tools in ~260 jobs, most tools have 1-2 positive examples, so a one-vs-rest
classifier predicts nothing (F1 = 0). Tools come from the dictionary extractor
(src.extraction.rules.detect_tools), which needs no training and is therefore scored on
every labeled job, not only on the held-out set."""
import argparse
import json

import joblib
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC

import config
from src.evaluation.score_gold import clean_tools_cell, load_gold, tool_scores
from src.extraction.rules import detect_tools


def _text(labels, jobs):
    """Join title and description while keeping labels aligned."""
    df = labels[["job_id", "title"]].copy()
    df = df.merge(jobs[["job_id", "description"]], on="job_id", how="left",
                  suffixes=("", "_jobs"))
    df["title"] = df["title"].fillna("")
    df["description"] = df["description"].fillna("")
    return (df["title"].astype(str) + "\n" + df["description"].astype(str)).str.strip()


def _classifier(model):
    return Pipeline([
        ("tfidf", TfidfVectorizer(lowercase=True, ngram_range=(1, 2),
                                  sublinear_tf=True)),
        ("model", model),
    ])


def _require_classes(name, values):
    classes = set(values)
    if len(classes) < 2:
        raise SystemExit(f"{name} needs at least two labeled classes; found {sorted(classes)}")


def _labeled_class_rows(labels, column):
    values = labels[column].fillna("").astype(str).str.strip()
    return labels.loc[values != ""].copy()


def _require_test_labels(labels, columns):
    missing = {
        column: int(labels[column].fillna("").astype(str).str.strip().eq("").sum())
        for column in columns
    }
    missing = {column: count for column, count in missing.items() if count}
    if missing:
        raise SystemExit(
            "The selected test file has unlabeled values: "
            f"{missing}. Complete the held-out labels before evaluation."
        )


def _all_labeled():
    """Every hand-labeled job (test, expanded, gold), one row per job_id."""
    frames = []
    for prefix in ("test", "expanded", "gold"):           # first one wins on duplicates
        try:
            df, _ = load_gold(prefix)
        except SystemExit:
            continue
        frames.append(df[["job_id", "title", "tools"]])
    return pd.concat(frames).drop_duplicates("job_id").reset_index(drop=True)


def _score_tool_rules(labels, jobs):
    """Score the dictionary extractor against hand labels (both in canonical spelling)."""
    df = labels[["job_id", "title", "tools"]].copy().reset_index(drop=True)
    df["tools"] = df["tools"].fillna("").map(clean_tools_cell)
    df["tools_pred"] = [", ".join(detect_tools(t)) for t in _text(df, jobs)]
    prec, rec, f1, tp, fp, fn, bad = tool_scores(df, "tools_pred")
    gold_sets = df["tools"].map(lambda c: {x.lower() for x in c.split(", ") if x})
    pred_sets = df["tools_pred"].map(lambda c: {x.lower() for x in c.split(", ") if x})
    no_tool = gold_sets.map(len) == 0
    metrics = {
        "method": "dictionary rules (detect_tools), lenient match",
        "jobs": len(df),
        "jobs_with_tools": int((~no_tool).sum()),
        "precision_micro": prec,
        "recall_micro": rec,
        "f1_micro": f1,
        "tp": tp, "fp": fp, "fn": fn,
        "exact_match_per_job": float((gold_sets == pred_sets).mean()),
        "no_tool_jobs_predicted_empty": float((no_tool & (pred_sets.map(len) == 0)).sum()
                                              / max(1, no_tool.sum())),
    }
    errors = pd.DataFrame(bad, columns=["job_id", "missed", "extra"])
    errors["missed"] = errors["missed"].map(", ".join)
    errors["extra"] = errors["extra"].map(", ".join)
    errors = errors.merge(df[["job_id", "title"]], on="job_id", how="left")
    return metrics, df["tools"].tolist(), df["tools_pred"].tolist(), errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train-set", default="gold", choices=["gold", "expanded"])
    ap.add_argument("--test-set", default="test", choices=["test"])
    args = ap.parse_args()

    train, train_source = load_gold(args.train_set)
    test, test_source = load_gold(args.test_set)
    _require_test_labels(test, ["seniority", "remote"])
    jobs = pd.read_csv(config.JOBS_CLEAN_CSV, dtype={"job_id": str},
                       usecols=["job_id", "description"])
    seniority_train = _labeled_class_rows(train, "seniority")
    remote_train = _labeled_class_rows(train, "remote")
    if seniority_train.empty or remote_train.empty:
        raise SystemExit("Add seniority and remote labels before training.")
    test_text = _text(test, jobs)

    _require_classes("Seniority classifier", seniority_train["seniority"])
    _require_classes("Remote classifier", remote_train["remote"])

    seniority = _classifier(LogisticRegression(max_iter=2000, class_weight="balanced"))
    remote = _classifier(LinearSVC(class_weight="balanced"))
    seniority.fit(_text(seniority_train, jobs), seniority_train["seniority"])
    remote.fit(_text(remote_train, jobs), remote_train["remote"])

    seniority_pred = seniority.predict(test_text)
    remote_pred = remote.predict(test_text)
    tools_test, tools_true, tools_pred, _ = _score_tool_rules(test, jobs)
    tools_all, _, _, tools_errors = _score_tool_rules(_all_labeled(), jobs)

    metrics = {
        "train_source": train_source,
        "test_source": test_source,
        "train_jobs": len(train),
        "test_jobs": len(test),
        "seniority": {
            "accuracy": accuracy_score(test["seniority"], seniority_pred),
            "macro_f1": f1_score(test["seniority"], seniority_pred, average="macro",
                                 zero_division=0),
        },
        "remote": {
            "accuracy": accuracy_score(test["remote"], remote_pred),
            "macro_f1": f1_score(test["remote"], remote_pred, average="macro",
                                 zero_division=0),
        },
        "tools_test": tools_test,
        "tools_all_labeled": tools_all,
    }

    config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump({
        "seniority": seniority,
        "remote": remote,
    }, config.CLASSIFIER_BUNDLE)
    predictions = pd.DataFrame({
        "job_id": test["job_id"].values,
        "seniority_true": test["seniority"].values,
        "seniority_pred": seniority_pred,
        "remote_true": test["remote"].values,
        "remote_pred": remote_pred,
        "tools_true": tools_true,
        "tools_pred": tools_pred,
    })
    predictions.to_csv(config.CLASSIFIER_PREDICTIONS, index=False, encoding="utf-8-sig")
    config.CLASSIFIER_METRICS.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    tools_errors.to_csv(config.TOOLS_RULE_ERRORS, index=False, encoding="utf-8-sig")

    print(json.dumps(metrics, indent=2))
    print(f"\nSaved model: {config.CLASSIFIER_BUNDLE}")
    print(f"Saved predictions: {config.CLASSIFIER_PREDICTIONS}")
    print(f"Saved tool errors (all labeled jobs): {config.TOOLS_RULE_ERRORS}")


if __name__ == "__main__":
    main()
