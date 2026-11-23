"""Salary bracket prediction for Tanitjobs postings (TND / month).

Tanitjobs only gives brackets ("Entre 1000 DT et 1500 DT"), so this is an ORDINAL
CLASSIFICATION task, not a regression on an exact amount. The small top/bottom brackets
are merged so every class has enough examples:

    0: < 1000 DT     1: 1000-1500 DT     2: 1500-2000 DT     3: >= 2000 DT

Training rows: jobs with salary_kind == 'bracket' (duplicates removed).

Model (SalaryMix): the average of a class-balanced logistic regression, which finds the rare high
brackets, and an ordinal one (P(pay >= 1000), P(>= 1500), P(>= 2000)), which avoids large errors.
The predicted bracket is the MEDIAN of the averaged distribution; the confidence is the probability
of that bracket. Chosen over the earlier plain logistic regression because it makes fewer large
errors and its confidence is closer to the real hit rate (macro F1 is about the same).

Evaluations:
  1. 5-fold GroupKFold by company: postings of one company are never in both the training and the
     validation fold, otherwise the model memorises the company's pay.
  2. Time-based hold-out: train on the older postings, test on the most recent 20%. Reported for
     all of them and for companies absent from training, which is the harder and more relevant
     case: most postings without a stated salary come from companies that never state one.
Metrics: accuracy, macro F1, within-one-bracket accuracy, mean absolute bracket error, and the
confidence error (average gap between the stated confidence and the real hit rate).

Features: title + description (TF-IDF), experience years, education level, category, region,
contract types, languages. Rule-extracted seniority, managerial title and tools did not improve
the score, so they are only evaluated with --compare-features.

The model is refit on all labeled jobs and predicts a bracket for jobs whose salary is
'confidential' or missing. With --write-db the predictions go to core.job_salary_prediction
(a separate table: stated salaries in core.fact_job are never changed).

    python -m src.models.train_salary
    python -m src.models.train_salary --write-db
    python -m src.models.train_salary --compare-features
"""
import argparse
import json

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from sklearn.linear_model import LogisticRegression
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
MODEL_NAME = "salary_bracket_v3"
CHOSEN = "salary_mix"
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


def load_jobs(extra=False):
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
    if not extra:
        return df
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


class SalaryMix:
    """Average of a balanced multinomial and an ordinal logistic regression; predicts the median bracket."""

    def __init__(self, extra=False, C=0.5):
        self.features = features(extra)
        self.C = C

    def fit(self, X, y):
        Z = self.features.fit_transform(X)
        self.multinomial = LogisticRegression(max_iter=3000, C=self.C, class_weight="balanced").fit(Z, y)
        self.thresholds = [LogisticRegression(max_iter=3000, C=self.C).fit(Z, y > k)
                           for k in range(len(BRACKETS) - 1)]
        return self

    def predict_proba(self, X):
        Z = self.features.transform(X)
        above = np.column_stack([m.predict_proba(Z)[:, 1] for m in self.thresholds])
        above = np.minimum.accumulate(above, axis=1)          # P(pay >= b) can only decrease with b
        above = np.hstack([np.ones((len(above), 1)), above, np.zeros((len(above), 1))])
        return (self.multinomial.predict_proba(Z) + above[:, :-1] - above[:, 1:]) / 2

    def predict(self, X):
        return np.argmax(self.predict_proba(X).cumsum(axis=1) >= 0.5, axis=1)


def logreg():
    return Pipeline([
        ("features", features(False)),
        ("model", LogisticRegression(max_iter=3000, C=2.0, class_weight="balanced")),
    ])


def candidates(extra=False):
    models = {
        "baseline_majority": lambda: DummyClassifier(strategy="most_frequent"),
        "logreg (previous model)": logreg,
        CHOSEN: SalaryMix,
    }
    if extra:
        models["salary_mix_+seniority_tools"] = lambda: SalaryMix(extra=True)
    return models


def confidence_error(hit, confidence, bins=10):
    """Expected calibration error: average gap between confidence and hit rate, per confidence band."""
    band = np.minimum((confidence * bins).astype(int), bins - 1)
    return float(sum((band == b).mean() * abs(confidence[band == b].mean() - hit[band == b].mean())
                     for b in np.unique(band)))


def scores(y_true, y_pred, confidence):
    return {
        "accuracy": accuracy_score(y_true, y_pred),
        "macro_f1": f1_score(y_true, y_pred, average="macro", zero_division=0),
        "within_one_bracket": float((np.abs(y_true - y_pred) <= 1).mean()),
        "mean_abs_bracket_error": float(np.abs(y_true - y_pred).mean()),
        "confidence_error": confidence_error(y_true == y_pred, confidence),
    }


def show(name, r):
    print(f"  {name:<28} acc {r['accuracy']:.3f} | macro F1 {r['macro_f1']:.3f} | "
          f"within 1 {r['within_one_bracket']:.3f} | MAE {r['mean_abs_bracket_error']:.3f} | "
          f"conf. error {r['confidence_error']:.3f}")


def predict(model, X):
    """-> (bracket index, probability the model gives to that bracket)"""
    pred = np.asarray(model.predict(X))
    return pred, model.predict_proba(X)[np.arange(len(pred)), pred]


def cross_validate(make_model, X, y, groups, folds=5):
    pred, conf = np.zeros(len(y), dtype=int), np.zeros(len(y))
    for train_idx, val_idx in GroupKFold(n_splits=folds).split(X, y, groups):
        model = make_model().fit(X.iloc[train_idx], y[train_idx])
        pred[val_idx], conf[val_idx] = predict(model, X.iloc[val_idx])
    return pred, conf


def time_holdout(models, labeled, y):
    """Train on the oldest postings, test on the newest TEST_SHARE of them: all, and unseen companies."""
    cutoff = labeled["date_posted"].quantile(1 - TEST_SHARE)
    train = (labeled["date_posted"] < cutoff).to_numpy()
    test = labeled[~train]
    unseen = ~test["company_key"].isin(set(labeled.loc[train, "company_key"])).to_numpy()
    results = {"all": {}, "unseen_companies": {}}
    for name, make in models.items():
        pred, conf = predict(make().fit(labeled[train], y[train]), test)
        results["all"][name] = scores(y[~train], pred, conf)
        results["unseen_companies"][name] = scores(y[~train][unseen], pred[unseen], conf[unseen])
    return {"test_from": f"{cutoff:%Y-%m-%d}", "train_jobs": int(train.sum()), "test_jobs": len(test),
            "unseen_company_jobs": int(unseen.sum()), "results": results}


def write_db(out):
    from src.database import db
    with db.connect() as conn:
        conn.execute(CREATE_TABLE)
        pk = dict(conn.execute(
            "SELECT source_job_id, job_pk FROM core.fact_job WHERE source_id = 1").fetchall())
        rows = [(pk[r.job_id], r.salary_bracket_pred, *BOUNDS[BRACKETS.index(r.salary_bracket_pred)],
                 r.confidence, MODEL_NAME)
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
    ap.add_argument("--compare-features", action="store_true",
                    help="also evaluate the model with rule-extracted seniority and tools")
    args = ap.parse_args()

    jobs = load_jobs(args.compare_features)
    models = candidates(args.compare_features)
    labeled = jobs[jobs["salary_kind"] == "bracket"].reset_index(drop=True)
    y = labeled["salary_min"].map(bracket_of).to_numpy()
    groups = labeled["company_key"].to_numpy()
    print(f"Labeled jobs: {len(labeled)} from {len(set(groups))} companies")
    print("Class counts:", {BRACKETS[k]: int(v) for k, v in zip(*np.unique(y, return_counts=True))})

    print("\n1) 5-fold cross-validation grouped by company")
    results, cv = {}, {}
    for name, make in models.items():
        cv[name] = cross_validate(make, labeled, y, groups)
        results[name] = scores(y, *cv[name])
        show(name, results[name])
    cm = confusion_matrix(y, cv[CHOSEN][0], labels=range(len(BRACKETS)))
    print(f"\nConfusion matrix of {CHOSEN}:")
    print(pd.DataFrame(cm, index=[f"true {b}" for b in BRACKETS],
                       columns=[f"pred {b}" for b in BRACKETS]).to_string())

    holdout = time_holdout(models, labeled, y)
    print(f"\n2) Time-based hold-out: trained on {holdout['train_jobs']} postings before {holdout['test_from']}")
    for part, n in [("all", holdout["test_jobs"]), ("unseen_companies", holdout["unseen_company_jobs"])]:
        print(f" {part} ({n} postings)")
        for name, r in holdout["results"][part].items():
            show(name, r)

    model = models[CHOSEN]().fit(labeled, y)
    unlabeled = jobs[jobs["salary_kind"] != "bracket"].reset_index(drop=True)
    out = unlabeled[["job_id", "title", "company", "salary_kind"]].copy()
    pred, conf = predict(model, unlabeled)
    out["salary_bracket_pred"] = [BRACKETS[i] for i in pred]
    out["confidence"] = conf.round(3)

    config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "brackets": BRACKETS, "name": CHOSEN, "version": MODEL_NAME},
                config.SALARY_MODEL)
    out.to_csv(config.SALARY_PREDICTIONS, index=False, encoding="utf-8-sig")
    config.SALARY_METRICS.write_text(json.dumps({
        "task": "ordinal salary bracket classification (Tanitjobs, TND/month)",
        "brackets": BRACKETS,
        "labeled_jobs": len(labeled),
        "model": CHOSEN,
        "cross_validation": {"method": "5-fold GroupKFold by company", "results": results},
        "confusion_matrix": cm.tolist(),
        "time_holdout": holdout,
    }, indent=2), encoding="utf-8")
    print(f"\nPredicted brackets for {len(out)} jobs without a stated salary:")
    print(out.groupby("salary_kind")["salary_bracket_pred"].value_counts().unstack(fill_value=0).to_string())
    print(f"\nSaved model: {config.SALARY_MODEL}\nSaved predictions: {config.SALARY_PREDICTIONS}"
          f"\nSaved metrics: {config.SALARY_METRICS}")
    if args.write_db:
        write_db(out)


if __name__ == "__main__":
    main()
