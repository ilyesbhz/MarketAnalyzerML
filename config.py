"""Central place for every path and setting. Edit USER_AGENT once, here."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# --- folders ---
DATA = ROOT / "data"
RAW_DIR = DATA / "raw"              # downloaded pages (.html.gz), never edited
INTERIM_DIR = DATA / "interim"      # URL list, logs
PROCESSED_DIR = DATA / "processed"  # clean dataset
GOLD_DIR = DATA / "gold"            # evaluation sample + reviewed labels
EXTRACTED_DIR = DATA / "extracted"  # LLM predictions on the labeled samples
MODEL_DIR = DATA / "models"

# --- files ---
JOB_URLS = INTERIM_DIR / "job_urls.json"
GONE_LOG = INTERIM_DIR / "gone.log"
JOBS_CLEAN_CSV = PROCESSED_DIR / "jobs_clean.csv"
GOLD_SAMPLE_CSV = GOLD_DIR / "gold_sample.csv"

# --- scraping ---
SITEMAP_URL = "https://www.tanitjobs.com/sitemap.xml"
# Put your real contact email here (polite scraping standard)
USER_AGENT = "Mozilla/5.0 (academic project; contact: ilyeszhb@gmail.com)"
SCRAPE_DELAY = 3            # seconds between requests
MAX_CONSECUTIVE_ERRORS = 5  # stop after this many errors in a row

# --- Adzuna (global module) ---
ADZUNA_RAW_DIR = RAW_DIR / "adzuna"
ADZUNA_CATEGORIES_JSON = INTERIM_DIR / "adzuna_categories.json"
ADZUNA_CALLS_LOG = INTERIM_DIR / "adzuna_calls.json"
ADZUNA_COUNTRIES = ["fr", "gb"]
ADZUNA_DAILY_BUDGET = 70       # max HTTP calls per day
ADZUNA_RESULTS_PER_PAGE = 50
ADZUNA_DELAY = 3               # seconds between calls

# --- LLM extraction (Ollama) ---
OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b"
GOLD_LABELS_CSV = GOLD_DIR / "gold_labels.csv"

# --- trained models ---
CLASSIFIER_BUNDLE = MODEL_DIR / "job_text_classifiers.joblib"
TOOLS_RULE_ERRORS = MODEL_DIR / "tools_rule_errors.csv"
SALARY_MODEL = MODEL_DIR / "salary_bracket_model.joblib"
SALARY_PREDICTIONS = MODEL_DIR / "salary_bracket_predictions.csv"
SALARY_METRICS = MODEL_DIR / "salary_bracket_metrics.json"