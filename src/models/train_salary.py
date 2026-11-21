"""Salary bracket prediction for Tanitjobs postings (TND / month).

Tanitjobs only gives brackets ("Entre 1000 DT et 1500 DT"), so this is an ORDINAL
CLASSIFICATION task, not a regression on an exact amount. The small top/bottom brackets
are merged so every class has enough examples:

    0: < 1000 DT     1: 1000-1500 DT     2: 1500-2000 DT     3: >= 2000 DT

Training rows: jobs with salary_kind == 'bracket' (duplicates removed).

Two evaluations:
  1. 5-fold GroupKFold by company (model selection): postings of one company are never in
     both the training and the validation fold, otherwise the model memorises the company's pay.
  2. Time-based hold-out (final check): train on the older postings, test on the most recent
     20%, which is how the model is used on new postings every day.
Metrics: accuracy, macro F1, within-one-bracket accuracy, mean absolute bracket error.

Features: title + description (TF-IDF), experience years, education level, category, region,
contract types, languages, and, from the rule extractor, seniority, managerial title and tools.

The best model is refit on all labeled jobs and predicts a bracket (with its probability) for
jobs whose salary is 'confidential' or missing. With --write-db the predictions go to
core.job_salary_prediction (a separate table: stated salaries in core.fact_job are never changed).

    python -m src.models.train_salary
    python -m src.models.train_salary --write-db
"""
import argparse
import json

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

import config
from src.extraction.rules import detect_tools, is_managerial, rule_seniority

BRACKETS = ["<1000", "1000-1500", "1500-2000", ">=2000"]
BOUNDS = {0: (None, 1000), 1: (1000, 1500), 2: (1500, 2000), 3: (2000, None)}   # TND / month
NUMERIC = ["experience_min_years", "education_level", "log_desc_len", "experience_missing",
           "education_missing"]
EXTRA_NUMERIC = ["n_tools", "managerial"]
MULTI = ["categories", "employment_types", "languages", "region"]
EXTRA_MULTI = ["seniority", "tools"]
MODEL_NAME = "salary_bracket_v2"
TEST_SHARE = 0.2

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS core.job_salary_prediction (
    job_pk        BIGINT PRIMARY KEY REFERENCES core.fact_job (job_pk) ON DELETE CASCADE,
    bracket       TEXT NOT NULL CHECK (bracket IN ('<1000', '1000-1500', '1500-2000', '>=2000')),
    salary_min    NUMERIC(12, 2),
    salary_max    NUMERIC(12, 2),
    currency      CHAR(3) NOT NULL DEFAULT 'TND',
    period        TEXT NOT NULL DEFAULT 'month',
    confidence    NUMERIC(4, 3),
    model         TEXT NOT NULL,
    predicted_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)"""
UPSERT = """
INSERT INTO core.job_salary_prediction (job_pk, bracket, salary_min, salary_max, confidence, model)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (job_pk) DO UPDATE SET bracket = EXCLUDED.bracket, salary_min = EXCLUDED.salary_min,
    salary_max = EXCLUDED.salary_max, confidence = EXCLUDED.confidence, model = EXCLUDED.model,
    predicted_at = now()"""


def split_pipe(value):
    """Multi-valued cells are stored as 'CDI|Temps plein|SIVP'. Module-level so joblib can pickle it."""
    return [x.strip() for x in str(value).split("|") if x.strip()]


def bracket_of(salary_min):
    """Bracket index from the lower bound of the Tanitjobs bracket."""
    m = float(salary_min)
    if m < 1000:
        return 0
    if m < 1500:
        return 1
    if m < 2000:
        return 2
    return 3


def load_jobs():
    df = pd.read_csv(config.JOBS_CLEAN_CSV, dtype=str)
    df = df[df["is_duplicate"].fillna("False") != "True"].copy()
    for col in ["title", "description"] + MULTI:
        df[col] = df[col].fillna("")
    for col in ["experience_min_years", "education_level"]:
        values = pd.to_numeric(df[col], errors="coerce")
        df[col.split("_")[0] + "_missing"] = values.isna().astype(float)
        df[col] = values.fillna(values.median())
    df["log_desc_len"] = np.log1p(pd.to_numeric(df["desc_len"], errors="coerce").fillna(0))
    df["company_key"] = df["company_key"].fillna(df["job_id"])   # unknown company = its own group
    df["date_posted"] = pd.to_datetime(df["date_posted"], errors="coerce")
    # rule-extractor features (same functions the pipeline uses, so they exist for every job)
    df["seniority"] = [rule_seniority(t, d) for t, d in zip(df["title"], df["description"])]
    df["managerial"] = df["title"].map(is_managerial).astype(float)
    tools = [detect_tools(f"{t}\n{d}") for t, d in zip(df["title"], df["description"])]
    df["tools"] = ["|".join(x) for x in tools]
    df["n_tools"] = [float(len(x)) for x in tools]
    return df


def features(extra):
    numeric = NUMERIC + (EXTRA_NUMERIC if extra else [])
    multi = MULTI + (EXTRA_MULTI if extra else [])
    multi_hot = [(c, CountVectorizer(tokenizer=split_pipe, lowercase=False, binary=True,
                                     token_pattern=None, min_df=2), c) for c in multi]
    return ColumnTransformer([
        ("title", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, min_df=2), "title"),
        ("desc", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, min_df=3,
                                 max_features=30000), "description"),
        ("num", StandardScaler(), numeric),
        *multi_hot,
    ])


class OrdinalRidge:
    """Regression on the bracket index, rounded back to a bracket: respects the order of classes."""

    def __init__(self, extra=True, alpha=3.0):
        self.pipe = Pipeline([("features", features(extra)), ("model", Ridge(alpha=alpha))])

    def fit(self, X, y):
        self.pipe.fit(X, y)
        return self

    def predict(self, X):
        return np.clip(np.rint(self.pipe.predict(X)), 0, len(BRACKETS) - 1).astype(int)


def logreg(extra):
    return Pipeline([
        ("features", features(extra)),
        ("model", LogisticRegression(max_iter=3000, C=2.0, class_weight="balanced")),
    ])


def candidates():
    return {
        "baseline_majority": lambda: DummyClassifier(strategy="most_frequent"),
        "logreg_base": lambda: logreg(extra=False),
        "logreg_+seniority_tools": lambda: logreg(extra=True),
        "ordinal_ridge_base": lambda: OrdinalRidge(extra=False),
        "ordinal_ridge_+seniority_tools": lambda: OrdinalRidge(extra=True),
    }


def scores(y_true, y_pred):
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    return {
        "accuracy": accuracy_score(y_true, y_pred),
        "macro_f1": f1_score(y_true, y_pred, average="macro", zero_division=0),
        "within_one_bracket": float((np.abs(y_true - y_pred) <= 1).mean()),
        "mean_abs_bracket_error": float(np.abs(y_true - y_pred).mean()),
    }


def show(name, r):
    print(f"  {name:<32} acc {r['accuracy']:.3f} | macro F1 {r['macro_f1']:.3f} | "
          f"within 1 {r['within_one_bracket']:.3f} | MAE {r['mean_abs_bracket_error']:.3f}")


def cross_validate(make_model, X, y, groups, folds=5):
    pred = np.zeros(len(y), dtype=int)
    for train_idx, val_idx in GroupKFold(n_splits=folds).split(X, y, groups):
        model = make_model().fit(X.iloc[train_idx], y[train_idx])
        pred[val_idx] = model.predict(X.iloc[val_idx])
    return pred


def time_holdout(make_model, labeled, y):
    """Train on the oldest postings, test on the newest TEST_SHARE of them."""
    cutoff = labeled["date_posted"].quantile(1 - TEST_SHARE)
    train, test = labeled["date_posted"] < cutoff, labeled["date_posted"] >= cutoff
    model = make_model().fit(labeled[train], y[train])
    return scores(y[test], model.predict(labeled[test])), cutoff, int(train.sum()), int(test.sum())


def write_db(out):
    from src.database import db
    with db.connect() as conn:
        conn.execute(CREATE_TABLE)
        pk = dict(conn.execute(
            "SELECT source_job_id, job_pk FROM core.fact_job WHERE source_id = 1").fetchall())
        rows = [(pk[r.job_id], r.salary_bracket_pred, *BOUNDS[BRACKETS.index(r.salary_bracket_pred)],
                 getattr(r, "confidence", None), MODEL_NAME)
                for r in out.itertuples() if r.job_id in pk]
        conn.cursor().executemany(UPSERT, rows)
        # a job that now states its salary must not keep an old prediction
        conn.execute("DELETE FROM core.job_salary_prediction p USING core.fact_job f "
                     "WHERE p.job_pk = f.job_pk AND f.salary_kind = 'bracket'")
        total = conn.execute("SELECT count(*) FROM core.job_salary_prediction").fetchone()[0]
    print(f"Database: wrote {len(rows)} predictions ({len(out) - len(rows)} jobs not in core.fact_job); "
          f"core.job_salary_prediction now has {total} rows")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-db", action="store_true",
                    help="store the predictions in core.job_salary_prediction")
    args = ap.parse_args()

    jobs = load_jobs()
    labeled = jobs[jobs["salary_kind"] == "bracket"].reset_index(drop=True)
    y = labeled["salary_min"].map(bracket_of).to_numpy()
    groups = labeled["company_key"].to_numpy()
    print(f"Labeled jobs: {len(labeled)} from {len(set(groups))} companies")
    print("Class counts:", {BRACKETS[k]: int(v) for k, v in zip(*np.unique(y, return_counts=True))})

    print("\n1) 5-fold cross-validation grouped by company")
    results, cv_preds = {}, {}
    for name, make in candidates().items():
        cv_preds[name] = cross_validate(make, labeled, y, groups)
        results[name] = scores(y, cv_preds[name])
        show(name, results[name])

    # macro F1 rounded to 0.01: smaller differences are noise at this sample size, and on a tie
    # max() keeps the first, i.e. the simpler model (candidates are listed simple-first)
    best = max((n for n in results if n != "baseline_majority"),
               key=lambda n: round(results[n]["macro_f1"], 2))
    print(f"\nBest model (macro F1): {best}")
    cm = confusion_matrix(y, cv_preds[best], labels=range(len(BRACKETS)))
    print(pd.DataFrame(cm, index=[f"true {b}" for b in BRACKETS],
                       columns=[f"pred {b}" for b in BRACKETS]).to_string())

    print(f"\n2) Time-based hold-out (newest {TEST_SHARE:.0%} of postings)")
    holdout = {}
    for name in ["baseline_majority", best]:
        holdout[name], cutoff, n_train, n_test = time_holdout(candidates()[name], labeled, y)
        show(name, holdout[name])
    print(f"  trained on {n_train} postings before {cutoff:%Y-%m-%d}, tested on {n_test} from that date on")

    # refit on all labeled jobs, predict the jobs without a stated salary
    model = candidates()[best]().fit(labeled, y)
    unlabeled = jobs[jobs["salary_kind"] != "bracket"].reset_index(drop=True)
    out = unlabeled[["job_id", "title", "company", "salary_kind"]].copy()
    out["salary_bracket_pred"] = [BRACKETS[i] for i in model.predict(unlabeled)]
    if hasattr(model, "predict_proba"):
        out["confidence"] = model.predict_proba(unlabeled).max(axis=1).round(3)

    config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "brackets": BRACKETS, "name": best, "version": MODEL_NAME},
                config.SALARY_MODEL)
    out.to_csv(config.SALARY_PREDICTIONS, index=False, encoding="utf-8-sig")
    config.SALARY_METRICS.write_text(json.dumps({
        "task": "ordinal salary bracket classification (Tanitjobs, TND/month)",
        "brackets": BRACKETS,
        "labeled_jobs": len(labeled),
        "cross_validation": {"method": "5-fold GroupKFold by company", "results": results},
        "best": best,
        "confusion_matrix_best": cm.tolist(),
        "time_holdout": {"test_from": f"{cutoff:%Y-%m-%d}", "train_jobs": n_train,
                         "test_jobs": n_test, "results": holdout},
    }, indent=2), encoding="utf-8")
    print(f"\nPredicted brackets for {len(out)} jobs without a stated salary:")
    print(out.groupby("salary_kind")["salary_bracket_pred"].value_counts().unstack(fill_value=0).to_string())
    print(f"\nSaved model: {config.SALARY_MODEL}\nSaved predictions: {config.SALARY_PREDICTIONS}"
          f"\nSaved metrics: {config.SALARY_METRICS}")
    if args.write_db:
        write_db(out)


if __name__ == "__main__":
    main()
