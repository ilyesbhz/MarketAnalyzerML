# Tunisian Job Market Analyzer

Data engineering + machine learning project on the Tunisian job market (all sectors).
Source: public job postings on Tanitjobs (collected via the public sitemap, respecting robots.txt).

## Pipeline

```
sitemap -> job URLs -> raw HTML pages -> parsed + cleaned dataset -> evaluation sample
                                                |
                         (next) LLM extraction -> PostgreSQL -> analysis -> salary model -> dashboard
```

| Step | Command (run from the project root) | Output |
|---|---|---|
| 1a. Get job URLs | `python -m src.collection.get_urls` | `data/interim/job_urls.json` |
| 1b. Download pages | `python -m src.collection.scrape_all` (`--limit 100` to test) | `data/raw/*.html.gz` |
| 2. Parse + clean | `python -m src.parsing.parse_all` | `data/processed/jobs_clean.csv` / `.json` |
| 3. Evaluation sample | `python -m src.evaluation.make_gold_sample` | `data/gold/gold_sample.csv` |

All paths and settings live in `config.py` (set your contact email in `USER_AGENT` before scraping).

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
  models/            (planned) salary bracket classifier
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
