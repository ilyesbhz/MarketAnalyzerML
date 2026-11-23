"""Evaluate seniority, remote-work and tools extraction on the hand-labeled samples.

The official method is the rules of src.extraction.rules, which is also what the pipeline stores.
Two references are reported next to them: the majority class, and a TF-IDF + linear model trained
on the labeled jobs. Tools have no trained model: ~120 distinct tools in ~260 jobs leave 1-2
examples for most of them.

    python -m src.models.train_classifiers                     old 30-job test set
    python -m src.models.train_classifiers --test-set fresh    new test set, as first labeled
    python -m src.models.train_classifiers --test-set fresh_reviewed    same jobs after the review

Two kinds of score, not to be mixed:
  - test set: jobs kept out of training. The tools dictionary was extended after reading errors on
    the old test set, so only the fresh set gives a fair score for tools; the old set also has just
    30 jobs, too few to separate methods that are close.
  - development jobs: cross-validated for the trained model, but the rules were tuned on these
    labels, so the rules' score is optimistic here. These jobs were also chosen to contain many
    level and work-mode words, so they do not represent the usual postings.

The rules never output 'manager' (a management title is stored separately, is_managerial), so jobs
labeled 'manager' are left out of the seniority score and used to score is_managerial instead.
"""
import argparse
import json

import joblib
import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC

import config
from src.evaluation.score_gold import clean_tools_cell, load_gold, tool_scores
from src.extraction.rules import detect_remote, detect_tools, is_managerial, rule_seniority

MODELS = {
    "seniority": lambda: LogisticRegression(max_iter=2000, class_weight="balanced"),
    "remote": lambda: LinearSVC(class_weight="balanced"),
}


def _classifier(task):
    return Pipeline([
        ("tfidf", TfidfVectorizer(lowercase=True, ngram_range=(1, 2), sublinear_tf=True)),
        ("model", MODELS[task]()),
    ])


def _load(prefix, jobs):
    """One labeled sample with its texts. Synthetic 'AUG' rows have no real posting and are dropped."""
    df, source = load_gold(prefix)
    df = df[~df["job_id"].str.startswith("AUG")]
    df = df.drop(columns="description", errors="ignore").merge(jobs, on="job_id", how="left")
    df["title"] = df["title"].fillna("")
    df["description"] = df["description"].fillna("")
    df["text"] = df["title"] + "\n" + df["description"]
    df["tools"] = df["tools"].map(clean_tools_cell)
    df["rules_seniority"] = [rule_seniority(t, d) for t, d in zip(df["title"], df["description"])]
    df["rules_remote"] = df["text"].map(detect_remote)
    df["rules_managerial"] = df["title"].map(is_managerial)
    return df, source


def _development(test_ids, jobs):
    frames = [_load(prefix, jobs)[0] for prefix in ("expanded", "gold")]
    df = pd.concat(frames).drop_duplicates("job_id")
    return df[~df["job_id"].isin(test_ids)].reset_index(drop=True)


def _rows(df, task):
    """Rows that count for a task: labeled, and for seniority not labeled 'manager'."""
    keep = df[task] != ""
    if task == "seniority":
        keep &= df[task] != "manager"
    return df[keep].reset_index(drop=True)


def _scores(y_true, y_pred):
    return {"accuracy": accuracy_score(y_true, y_pred),
            "macro_f1": f1_score(y_true, y_pred, average="macro", zero_division=0)}


def _cross_validate(task, df, folds=5):
    y, pred = df[task].to_numpy(), np.empty(len(df), dtype=object)
    for train, val in StratifiedKFold(folds, shuffle=True, random_state=0).split(df["text"], y):
        pred[val] = _classifier(task).fit(df["text"].iloc[train], y[train]).predict(df["text"].iloc[val])
    return pred


def _score_task(task, dev, test):
    train, held_out = _rows(dev, task), _rows(test, task)
    model = _classifier(task).fit(train["text"], train[task])
    majority = train[task].mode()[0]
    result = {
        "test": {
            "jobs": len(held_out),
            "rules": _scores(held_out[task], held_out[f"rules_{task}"]),
            "trained_model": _scores(held_out[task], model.predict(held_out["text"])),
            "majority": _scores(held_out[task], [majority] * len(held_out)),
        },
        "development": {
            "jobs": len(train),
            "rules": _scores(train[task], train[f"rules_{task}"]),
            "trained_model": _scores(train[task], _cross_validate(task, train)),
            "majority": _scores(train[task], [majority] * len(train)),
        },
    }
    return result, model


def _score_managerial(df):
    """is_managerial against the 'is_manager' column (fresh set) or the old 'manager' seniority label."""
    if "is_manager" in df.columns:
        df = df[df["is_manager"] != ""]
        truth = df["is_manager"].str.strip().str.lower() == "yes"
    else:
        truth = df["seniority"] == "manager"
    pred = df["rules_managerial"].astype(bool)
    hits = int((truth & pred).sum())
    return {"jobs": len(df), "managers": int(truth.sum()),
            "precision": hits / max(1, int(pred.sum())), "recall": hits / max(1, int(truth.sum()))}


def _score_tools(df):
    df = df[["job_id", "title", "text", "tools"]].copy()
    df["tools_pred"] = [", ".join(detect_tools(t)) for t in df["text"]]
    prec, rec, f1, tp, fp, fn, bad = tool_scores(df, "tools_pred")
    gold, pred = (df[c].map(lambda v: {x.lower() for x in v.split(", ") if x}) for c in ("tools", "tools_pred"))
    metrics = {"jobs": len(df), "jobs_with_tools": int((gold.map(len) > 0).sum()),
               "precision_micro": prec, "recall_micro": rec, "f1_micro": f1,
               "tp": tp, "fp": fp, "fn": fn, "exact_match_per_job": float((gold == pred).mean())}
    errors = pd.DataFrame(bad, columns=["job_id", "missed", "extra"])
    for col in ("missed", "extra"):
        errors[col] = errors[col].map(", ".join)
    return metrics, errors.merge(df[["job_id", "title"]], on="job_id", how="left"), df["tools_pred"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-set", default="test", choices=["test", "fresh", "fresh_reviewed"])
    args = ap.parse_args()

    jobs = pd.read_csv(config.JOBS_CLEAN_CSV, dtype={"job_id": str}, usecols=["job_id", "description"])
    test, source = _load(args.test_set, jobs)
    missing = {c: int((test[c] == "").sum()) for c in ("seniority", "remote", "is_manager") if c in test}
    if any(missing.values()):
        raise SystemExit(f"{source} still has unlabeled cells: {missing}. Finish labeling first.")
    dev = _development(set(test["job_id"]), jobs)

    # headline scores use the random part of the sample; the extra work-mode jobs are scored apart
    main_test = test[test["sample"] == "random"].reset_index(drop=True) if "sample" in test else test
    metrics, models = {"test_source": source, "test_jobs": len(main_test), "development_jobs": len(dev)}, {}
    for task in MODELS:
        metrics[task], models[task] = _score_task(task, dev, main_test)
    if "sample" in test:
        extra = test[test["sample"] != "random"]
        metrics["remote"]["test_workmode_jobs"] = {
            "jobs": len(extra), "rules": _scores(extra["remote"], extra["rules_remote"])}
    metrics["is_managerial"] = _score_managerial(main_test)
    metrics["tools_test"], _, tools_pred = _score_tools(main_test)
    metrics["tools_development"], tool_errors, _ = _score_tools(dev)

    config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(models, config.CLASSIFIER_BUNDLE)
    prefix = args.test_set
    predictions = main_test[["job_id", "title", "seniority", "rules_seniority", "remote", "rules_remote",
                             "rules_managerial", "tools"]].assign(rules_tools=tools_pred.values)
    for task in MODELS:
        predictions[f"trained_{task}"] = models[task].predict(main_test["text"])
    predictions.to_csv(config.MODEL_DIR / f"{prefix}_classifier_predictions.csv", index=False, encoding="utf-8-sig")
    metrics_file = config.MODEL_DIR / f"{prefix}_classifier_metrics.json"
    metrics_file.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    tool_errors.to_csv(config.TOOLS_RULE_ERRORS, index=False, encoding="utf-8-sig")

    for task in MODELS:
        for part in ("test", "development"):
            r = metrics[task][part]
            print(f"{task} / {part} ({r['jobs']} jobs)")
            for method in ("rules", "trained_model", "majority"):
                print(f"  {method:<14} accuracy {r[method]['accuracy']:.3f} | macro F1 {r[method]['macro_f1']:.3f}")
    print("is_managerial:", {k: round(v, 3) for k, v in metrics["is_managerial"].items()})
    for part in ("tools_test", "tools_development"):
        print(f"{part}: {metrics[part]['jobs']} jobs | F1 {metrics[part]['f1_micro']:.3f}")
    print(f"\nSaved metrics: {metrics_file}")


if __name__ == "__main__":
    main()
