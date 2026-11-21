# Tunisian Job Market Analyzer

Data engineering + machine learning project on the Tunisian job market (all sectors).
Source: public job postings on Tanitjobs (collected via the public sitemap, respecting robots.txt).

## Pipeline

```
sitemap -> job URLs -> raw HTML pages -> parsed + cleaned dataset -> evaluation sample
                                                |
                         (next) LLM + trained text classifiers -> PostgreSQL -> analysis -> dashboard
```

| Step | Command (run from the project root) | Output |
|---|---|---|
| 1a. Get job URLs | `python -m src.collection.get_urls` | `data/interim/job_urls.json` |
| 1b. Download pages | `python -m src.collection.scrape_all` (`--limit 100` to test) | `data/raw/*.html.gz` |
| 2. Parse + clean | `python -m src.parsing.parse_all` | `data/processed/jobs_clean.csv` / `.json` |
| 3. Evaluation sample | `python -m src.evaluation.make_gold_sample` | `data/gold/gold_sample.csv` |

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
`src/extraction/rules.py`), scored on every labeled job (F1 ≈ 0.81). Models, test
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
  not improve the score, so the simpler model is kept.
- Evaluation: 5-fold cross-validation grouped by company (a company's postings are never
  in both training and validation), then a time-based hold-out (train on older postings,
  test on the newest 20%). Compared against an "always the most frequent bracket" baseline.
- Results (Logistic Regression): macro F1 ≈ 0.50 in cross-validation and ≈ 0.56 on the
  newest postings, against 0.17 for the baseline; about 93% of predictions are at most one
  bracket off. `1500-2000` is the hardest class.
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
src/
  collection/        get_urls.py, scrape_all.py
  parsing/           parse_job.py (one page), parse_all.py (batch + cleaning + report)
  evaluation/        make_gold_sample.py
  extraction/        (planned) local LLM: seniority, skills, tools
  database/          (planned) PostgreSQL schema and loading
  analysis/          (planned) top skills, sectors, cities
  models/            train_classifiers.py (seniority, remote), train_salary.py (salary bracket)
  dashboard/         (planned) Streamlit / Power BI
data/
  raw/               downloaded pages, never edited
  interim/           URL list, logs
  processed/         clean dataset
  gold/              evaluation sample and reviewed labels
  extracted/         (planned) LLM output
exploration/         one-off scripts used while investigating the site
docs/                report
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

## Status

Done: collection, parsing, cleaning (3,321 jobs, 28 Aug - 10 Oct 2026), evaluation sample.
Next: local LLM extraction (Ollama), PostgreSQL, analysis, salary model, dashboard, daily scraping.
"# MarketAnalyzerML" 
