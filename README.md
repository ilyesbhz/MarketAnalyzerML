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

## Trained classifiers

Create a larger labeling workbook containing new recent and representative jobs:

```
python -m src.evaluation.make_expanded_sample --size 200
```

Label the new rows in `data/gold/expanded_sample_labeled.xlsx` without looking at
predictions. Keep the existing test sample untouched. Then train with the expanded
labels:

```
python -m src.models.train_classifiers --train-set expanded
```

Train the three classical NLP models using the manually labeled development sample and
evaluate them on the held-out test sample:

```
python -m src.models.train_classifiers
```

The command trains TF-IDF + Logistic Regression for seniority and TF-IDF + Linear SVM
for remote-work type. Tools are not learned: with ~120 distinct tools in ~260 labeled
jobs most tools have only 1-2 examples, so a one-vs-rest classifier predicted nothing
(F1 = 0). Tools come from the dictionary extractor (`detect_tools` in
`src/extraction/rules.py`), scored on every labeled job (F1 ≈ 0.82, lenient match). This is
NOT a held-out score: part of the dictionary was added after reading the errors on these same
jobs, test set included. Without those additions the test set (30 jobs) scores F1 ≈ 0.53;
a fair estimate needs newly labeled jobs. Models, test
predictions, metrics and the list of tool errors (`tools_rule_errors.csv`) are saved
under `data/models/`.

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
- Evaluation: 5-fold cross-validation grouped by company (a company's postings are never
  in both training and validation), then a time-based hold-out (train on older postings,
  test on the newest 20%). Compared against an "always the most frequent bracket" baseline.
- Results (Logistic Regression): macro F1 ≈ 0.50 in cross-validation and ≈ 0.54 on the
  newest postings, against 0.17 for the baseline; about 92% of predictions are at most one
  bracket off in cross-validation (94% on the newest postings). `1500-2000` is the hardest class.
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
  models/            train_classifiers.py (seniority, remote), train_salary.py (salary bracket)
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
