-- Unified schema: Tanitjobs (scraping) and Adzuna (API) land in the same tables.
-- Safe to run many times (everything is IF NOT EXISTS / ON CONFLICT).

CREATE SCHEMA IF NOT EXISTS core;   -- cleaned, analysis-ready data
CREATE SCHEMA IF NOT EXISTS ops;    -- pipeline monitoring

-- ---------- dimensions ----------
CREATE TABLE IF NOT EXISTS core.dim_source (
    source_id SMALLINT PRIMARY KEY,
    name      TEXT NOT NULL UNIQUE,
    kind      TEXT NOT NULL CHECK (kind IN ('scraping', 'api'))
);
INSERT INTO core.dim_source (source_id, name, kind) VALUES
    (1, 'tanitjobs', 'scraping'),
    (2, 'adzuna',    'api')
ON CONFLICT (source_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS core.dim_sector (
    sector_id SERIAL PRIMARY KEY,
    name      TEXT NOT NULL UNIQUE
);

-- reference tables: source-specific category -> unified sector
CREATE TABLE IF NOT EXISTS core.map_tanitjobs_category (
    category_key   TEXT PRIMARY KEY,          -- normalized text
    category_label TEXT NOT NULL,             -- original text
    sector_id      INT  NOT NULL REFERENCES core.dim_sector (sector_id)
);
CREATE TABLE IF NOT EXISTS core.map_adzuna_tag (
    tag       TEXT PRIMARY KEY,
    sector_id INT NOT NULL REFERENCES core.dim_sector (sector_id)
);

CREATE TABLE IF NOT EXISTS core.dim_company (
    company_id   SERIAL PRIMARY KEY,
    name         TEXT    NOT NULL,
    name_key     TEXT    NOT NULL UNIQUE,     -- lower-cased, trimmed
    is_anonymous BOOLEAN NOT NULL DEFAULT FALSE
);

-- ---------- fact table: one row per job posting ----------
CREATE TABLE IF NOT EXISTS core.fact_job (
    job_pk               BIGSERIAL PRIMARY KEY,
    source_id            SMALLINT NOT NULL REFERENCES core.dim_source (source_id),
    source_job_id        TEXT     NOT NULL,   -- the id used by the source site
    title                TEXT     NOT NULL,
    company_id           INT REFERENCES core.dim_company (company_id),
    country              CHAR(2)  NOT NULL,   -- tn, fr, gb ...
    region               TEXT,
    city                 TEXT,
    date_posted          DATE,
    valid_through        DATE,
    employment_types     TEXT[],
    languages            TEXT[],
    experience_min_years SMALLINT,
    education_level      SMALLINT,
    salary_min           NUMERIC(12, 2),
    salary_max           NUMERIC(12, 2),
    salary_currency      CHAR(3),             -- TND, EUR, GBP ...
    salary_kind          TEXT NOT NULL DEFAULT 'none'
        CHECK (salary_kind IN ('bracket', 'exact', 'predicted', 'confidential', 'none')),
    applicants           SMALLINT,            -- capped at 50 on Tanitjobs
    description          TEXT,                -- Adzuna: truncated to ~500 chars
    desc_len             INT,
    primary_sector_id    INT REFERENCES core.dim_sector (sector_id),
    url                  TEXT,
    parsed_from          TEXT,
    is_duplicate         BOOLEAN NOT NULL DEFAULT FALSE,
    first_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, source_job_id),
    CHECK (salary_min IS NULL OR salary_max IS NULL OR salary_min <= salary_max)
);
CREATE INDEX IF NOT EXISTS ix_job_country ON core.fact_job (country);
CREATE INDEX IF NOT EXISTS ix_job_sector  ON core.fact_job (primary_sector_id);
CREATE INDEX IF NOT EXISTS ix_job_posted  ON core.fact_job (date_posted);
CREATE INDEX IF NOT EXISTS ix_job_company ON core.fact_job (company_id);

-- a job can belong to several sectors (Tanitjobs allows several categories)
CREATE TABLE IF NOT EXISTS core.job_sector (
    job_pk    BIGINT NOT NULL REFERENCES core.fact_job (job_pk) ON DELETE CASCADE,
    sector_id INT    NOT NULL REFERENCES core.dim_sector (sector_id),
    PRIMARY KEY (job_pk, sector_id)
);

-- ---------- monitoring: one row per pipeline step execution ----------
CREATE TABLE IF NOT EXISTS ops.pipeline_run (
    run_id      BIGSERIAL PRIMARY KEY,
    step        TEXT NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status      TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'ok', 'failed')),
    rows_in     INT,
    rows_out    INT,
    note        TEXT
);

-- ---------- LLM + rules extraction, one row per job ----------
CREATE TABLE IF NOT EXISTS core.job_extraction (
    job_pk          BIGINT PRIMARY KEY REFERENCES core.fact_job (job_pk) ON DELETE CASCADE,
    seniority       TEXT NOT NULL CHECK (seniority IN ('intern','junior','mid','senior','manager','unspecified')),
    seniority_llm   TEXT,                       -- the model's own answer, kept for comparison only
    remote          TEXT NOT NULL CHECK (remote IN ('onsite','hybrid','remote','unspecified')),
    tools           TEXT[] NOT NULL DEFAULT '{}',
    skills          TEXT[] NOT NULL DEFAULT '{}',
    model           TEXT NOT NULL,
    parse_failed    BOOLEAN NOT NULL DEFAULT FALSE,
    extracted_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_extraction_seniority ON core.job_extraction (seniority);

-- ---------- salary period (added when Adzuna was loaded) ----------
-- Adzuna salaries are yearly amounts. Tanitjobs brackets are assumed MONTHLY (the site does not
-- say; Tunisian ads quote monthly pay). Never compare the two without converting.
ALTER TABLE core.fact_job ADD COLUMN IF NOT EXISTS salary_period TEXT
    CHECK (salary_period IN ('month', 'year'));
UPDATE core.fact_job SET salary_period = 'month'
WHERE source_id = 1 AND salary_kind = 'bracket' AND salary_period IS NULL;

-- ---------- run log: a step can also be skipped (for example the extraction when Ollama is off) ----------
ALTER TABLE ops.pipeline_run DROP CONSTRAINT IF EXISTS pipeline_run_status_check;
ALTER TABLE ops.pipeline_run ADD CONSTRAINT pipeline_run_status_check
    CHECK (status IN ('running', 'ok', 'failed', 'skipped'));

-- ---------- standardized salaries ----------
-- Adzuna gives ONE number per posting with no unit: it can be hourly, monthly or yearly. The unit
-- is guessed from the size of the number (rules below, checked on real data: about 43% of French
-- stated salaries are not yearly). salary_annual is a full-time-equivalent yearly amount, NULL
-- when the unit is unclear. Tanitjobs brackets are assumed monthly. The raw values stay untouched
-- in core.fact_job; fact_job.salary_period is NOT reliable for Adzuna: use this view.
-- Hours per year: 1820 (35 h x 52) for France and others, 1950 (37.5 h x 52) for the UK.
CREATE OR REPLACE VIEW core.v_job_salary AS
WITH base AS (
    SELECT job_pk, source_id, country, salary_currency, salary_kind,
           (coalesce(salary_min, salary_max) + coalesce(salary_max, salary_min)) / 2 AS mid
    FROM core.fact_job
    WHERE salary_kind IN ('bracket', 'exact')
), classified AS (
    SELECT *, CASE
        WHEN source_id = 1 THEN 'month'                       -- Tanitjobs brackets (assumed monthly)
        WHEN mid < 5 THEN 'unclear'
        WHEN mid < 100 THEN 'hour'
        WHEN mid < 1000 THEN 'unclear'                        -- daily? weekly? part-time? cannot tell
        WHEN mid < 5000 THEN 'month'
        WHEN mid < 15000 THEN CASE WHEN country = 'gb' THEN 'year' ELSE 'unclear' END
        ELSE 'year'
    END AS unit
    FROM base
)
SELECT job_pk, source_id, country, salary_currency, salary_kind,
       mid AS salary_mid, unit AS salary_unit,
       CASE unit
           WHEN 'year'  THEN mid
           WHEN 'month' THEN mid * 12
           WHEN 'hour'  THEN mid * CASE country WHEN 'gb' THEN 1950 ELSE 1820 END
       END AS salary_annual
FROM classified;

-- ---------- data-quality check results (one row per check per run) ----------
CREATE TABLE IF NOT EXISTS ops.quality_check (
    check_id    BIGSERIAL PRIMARY KEY,
    run_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    name        TEXT NOT NULL,
    severity    TEXT NOT NULL CHECK (severity IN ('error', 'warning')),
    status      TEXT NOT NULL CHECK (status IN ('pass', 'fail')),
    value       NUMERIC,
    expectation TEXT NOT NULL,
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS ix_quality_run ON ops.quality_check (run_at);
