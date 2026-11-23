# Tunisian Job Market Analyzer

Data engineering + machine learning project on the Tunisian job market (all sectors).
Source: public job postings on Tanitjobs (collected via the public sitemap, respecting robots.txt).

## Pipeline

```
Tanitjobs sitemap -> raw pages -> jobs_clean.csv --+
                                                   +-> PostgreSQL -> extraction (rules + local LLM)
Adzuna API -> raw results -------------------------+        |
                                                            +-> salary model -> quality checks
                                                            +-> JSON exports -> web app import
```

Run everything (this is what Windows Task Scheduler runs through `run_daily.bat`):

```
python -m src.pipeline.run_daily
python -m src.pipeline.run_daily --only tanitjobs      one branch: tanitjobs | adzuna | quality
```

First-time database setup: `python -m src.database.init_db`. To inspect the data:
`python -m src.database.summary` and `python -m src.database.show_views`.

All paths and settings live in `config.py` (set your contact email in `USER_AGENT` before scraping).

## Seniority, remote work and tools

These come from the rules in `src/extraction/rules.py` (the pipeline stores their result). The
command below scores them and compares them with the majority class and with a trained model
(TF-IDF + Logistic Regression for seniority, + Linear SVM for remote work):

```
python -m src.models.train_classifiers                     old 30-job test set
python -m src.models.train_classifiers --test-set fresh    fresh test set, once labeled
```

| Accuracy / macro F1 | Rules | Trained model | Majority class |
|---|---|---|---|
| Seniority, test (25 jobs) | 0.80 / 0.63 | 0.80 / 0.64 | 0.72 / 0.21 |
| Seniority, development (236 jobs) | 0.89 / 0.81 | 0.77 / 0.43 | 0.72 / 0.17 |
| Remote, test (30 jobs) | 0.90 / 0.65 | 0.77 / 0.29 | 0.87 / 0.31 |
| Remote, development (250 jobs) | 0.76 / 0.64 | 0.70 / 0.51 | 0.63 / 0.19 |

The development scores of the rules are optimistic (the rules were tuned on those labels), and the
development jobs were chosen to contain many level and work-mode words, so they do not represent
the usual postings. The old test set is too small to separate close methods. Jobs labeled `manager`
are left out of the seniority score: the rules report management separately (`is_managerial`).

Tools have no trained model: with ~120 distinct tools in ~260 labeled jobs, a one-vs-rest
classifier predicted nothing (F1 = 0). The dictionary scores F1 ≈ 0.81 on the labeled jobs, but
part of it was added after reading the errors on these same jobs, test set included; without those
additions the 30 test jobs score F1 ≈ 0.53.

**Fresh test set.** `data/gold/fresh_sample_labeled.xlsx` (made by
`python -m src.evaluation.make_fresh_sample`) holds 200 never-labeled jobs: 150 random ones for the
headline score and 50 containing a work-mode word, scored apart. It was labeled without looking at
predictions, then reviewed against the Legend (`make_fresh_review`, flags taken from the Legend, not
from the rules) into `fresh_reviewed_sample_labeled.xlsx`. Scores on the reviewed labels, with the
rules as they were before seeing this set:

| Fresh test set (reviewed labels) | Rules | Majority class |
|---|---|---|
| Seniority, 150 random jobs (accuracy / macro F1) | 0.88 / 0.70 | 0.79 / 0.18 |
| Remote, 150 random jobs (accuracy) | 0.98 | 0.98 |
| Remote, 50 work-mode jobs (accuracy) | 0.66 | 0.44 |
| Management title, 26 managers in 150 jobs | precision 0.86, recall 0.92 | |
| Tools (F1, micro) | 0.79 | |

Only 4 of the 150 random postings state a work mode, so the random part cannot measure remote
detection; the 50 work-mode jobs do. The remote rules were then fixed using errors seen on this set
("presentiel", "base sur site", a work mode in the title, "hybrid in-office/remote"): the work-mode
part rose to 0.92, but that figure is no longer a held-out score. The development jobs, not used for
this fix, rose from 0.72 to 0.76. A new fresh sample is needed for a clean remote score.

## Salary bracket model

Tanitjobs gives salaries only as brackets (TND / month), and about 20% of postings state
one. The model predicts the bracket of the others:

```
python -m src.models.train_salary              train, evaluate, save predictions to CSV
python -m src.models.train_salary --write-db   same, and store them in the database
```

- Classes: `<1000`, `1000-1500`, `1500-2000`, `>=2000` (the small top/bottom brackets are merged).
- Features: title and description (TF-IDF), experience years, education level, category,
  region, contract types, languages. Rule-based seniority and tools were tested and did
  not improve the score; rerun that comparison with `--compare-features`.
- Model: the average of a balanced logistic regression and an ordinal one; the prediction is the
  median bracket, the confidence its probability.
- Evaluation: 5-fold cross-validation grouped by company, then a time-based hold-out (train on
  older postings, test on the newest 20%), reported for all postings and for companies the model
  has never seen. The second is closer to real use: about 85% of the postings without a stated
  salary come from companies that never state one.

| Accuracy / macro F1 / within one bracket | New model | Previous (logistic regression) | Majority class |
|---|---|---|---|
| Cross-validation (1,458 jobs) | 0.62 / 0.51 / 0.94 | 0.62 / 0.50 / 0.92 | 0.48 / 0.16 / 0.79 |
| Newest postings (296 jobs) | 0.65 / 0.53 / 0.95 | 0.63 / 0.54 / 0.94 | 0.51 / 0.17 / 0.85 |
| Newest, unseen companies (164 jobs) | 0.63 / 0.48 / 0.94 | 0.57 / 0.40 / 0.92 | 0.53 / 0.17 / 0.88 |

- The confidence is off by about 4 points on average (9 before). `1500-2000` stays the weakest
  class: right about 28% of the time when predicted. The design was chosen using these same jobs,
  so expect slightly lower scores on new data.
- Output: `data/models/salary_bracket_*` (model, metrics, predictions) and the table
  `core.job_salary_prediction`. Estimates are kept apart from stated salaries in
  `core.fact_job` and from Adzuna's own estimates; show them as "estimated" and consider
  filtering on `confidence`.

The daily pipeline retrains the model and refreshes the table (step `salary_model`), exports
`jobs.json` and the analytics files, then imports `jobs.json` into the web app's database
(step `import_app`, which runs `npm run import:jobs` in the folder above `APP_DATA_DIR`).

## Folder structure

```
config.py            paths and settings
run_daily.bat        entry point for Windows Task Scheduler
sql/                 database schema, analytics views, dashboard role
src/
  collection/        Tanitjobs URLs and pages, Adzuna API
  parsing/           parse_job.py (one page), parse_all.py (batch + cleaning + report)
  common/            sector mapping
  database/          schema setup, loading, extraction, summaries
  extraction/        rules.py (dictionary and rules), llm_extract.py (Ollama)
  evaluation/        labeling samples and scoring
  models/            train_classifiers.py (rules vs trained models), train_salary.py (salary bracket)
  quality/           data-quality checks
  export/            JSON exports and web app import
  pipeline/          run_daily.py
data/
  raw/               downloaded pages, never edited (not in git)
  interim/           URL list, API call log, Adzuna categories
  processed/         clean dataset
  reference/         sector mappings
  gold/              hand-labeled samples
  extracted/         LLM predictions on the labeled samples
  models/            trained models, metrics, predictions
exports/             JSON files read by the web app
docs/                project notes and experiment log
```

## Setup

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Scraping rules followed

- robots.txt read first: job pages are allowed; search-parameter URLs and CV pages are never used.
- 1 request every 3 seconds, honest User-Agent with a contact email, resumable, stops after repeated errors.
- Raw pages are saved once so parsing can be improved without contacting the site again.
