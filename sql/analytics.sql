-- =====================================================================================
-- ANALYTICS LAYER: the only thing the dashboards read.
-- Built on core.* and recreated at every init_db. Nothing here stores data.
-- Rules baked in: duplicates are excluded; salaries are standardized yearly full-time amounts
-- (core.v_job_salary); "active" means still listed (Tanitjobs: seen in the sitemap in the last
-- 3 days; Adzuna: seen in the last 14 days, because the API only returns the newest postings).
-- Adzuna sector sizes depend on our sampling (50 postings per category and day): compare
-- sectors WITHIN a country on salary and mix, never on how many jobs they have.
-- =====================================================================================
CREATE SCHEMA IF NOT EXISTS analytics;
DROP VIEW IF EXISTS analytics.jobs CASCADE;      -- CASCADE drops every view built on it; they are recreated below

-- pay_percentile: where a job's salary sits among all stated salaries of its country (0 = lowest,
-- 100 = highest, ties get their middle rank). It works for Tanitjobs brackets (a handful of distinct
-- values, where medians collapse to the same number everywhere), for outliers, and it needs no
-- currency conversion, so sectors can be compared inside a country and across countries.
CREATE OR REPLACE VIEW analytics.jobs AS
WITH base AS (
    SELECT f.job_pk, s.name AS source, f.country, f.title, c.name AS company, f.region, f.city,
           d.name AS sector, f.date_posted, current_date - f.date_posted AS age_days,
           f.first_seen_at::date AS first_seen, f.last_seen_at::date AS last_seen,
           CASE WHEN f.source_id = 1 THEN f.last_seen_at >= now() - interval '3 days'
                ELSE f.last_seen_at >= now() - interval '14 days' END AS is_active,
           e.seniority, e.remote, e.tools, e.skills,
           f.experience_min_years, f.education_level, f.employment_types, f.languages,
           f.salary_currency AS currency, f.salary_kind, v.salary_unit, v.salary_annual,
           -- model estimate (Tanitjobs jobs without a stated bracket); never used in the salary figures above
           sp.bracket AS salary_bracket_estimated, sp.confidence AS salary_estimate_confidence
    FROM core.fact_job f
    JOIN core.dim_source s ON s.source_id = f.source_id
    LEFT JOIN core.dim_company c ON c.company_id = f.company_id
    LEFT JOIN core.dim_sector d ON d.sector_id = f.primary_sector_id
    LEFT JOIN core.job_extraction e ON e.job_pk = f.job_pk
    LEFT JOIN core.v_job_salary v ON v.job_pk = f.job_pk
    LEFT JOIN core.job_salary_prediction sp ON sp.job_pk = f.job_pk
    WHERE NOT f.is_duplicate
), ranked AS (
    SELECT job_pk, round((100 * (percent_rank() OVER w + cume_dist() OVER w) / 2)::numeric, 1) AS pay_percentile
    FROM base WHERE salary_annual IS NOT NULL
    WINDOW w AS (PARTITION BY country ORDER BY salary_annual)
)
SELECT base.*, ranked.pay_percentile
FROM base LEFT JOIN ranked ON ranked.job_pk = base.job_pk;

-- headline numbers per country
CREATE OR REPLACE VIEW analytics.market_overview AS
SELECT country, min(source) AS source, count(*) AS jobs, count(*) FILTER (WHERE is_active) AS active_jobs,
       count(DISTINCT company) AS companies, min(date_posted) AS first_posting, max(date_posted) AS last_posting,
       count(salary_annual) AS jobs_with_salary,
       round(100.0 * count(salary_annual) / count(*), 1) AS pct_with_salary,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY salary_annual))::numeric) AS median_salary_per_year,
       min(currency) AS currency,
       count(tools) AS jobs_extracted
FROM analytics.jobs GROUP BY country;

-- salary and volume per sector, with a salary INDEX that lets the three countries be compared
-- without converting currencies: sector median divided by the country's overall median
CREATE OR REPLACE VIEW analytics.sector_summary AS
WITH per_sector AS (
    SELECT country, sector, count(*) AS jobs, count(*) FILTER (WHERE is_active) AS active_jobs,
           count(salary_annual) AS jobs_with_salary,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY salary_annual) AS median_salary,
           avg(pay_percentile) AS avg_pay_percentile,
           count(tools) AS jobs_extracted,
           count(*) FILTER (WHERE cardinality(tools) > 0) AS jobs_naming_a_tool
    FROM analytics.jobs GROUP BY country, sector
), per_country AS (
    SELECT country, percentile_cont(0.5) WITHIN GROUP (ORDER BY salary_annual) AS country_median
    FROM analytics.jobs GROUP BY country
)
SELECT p.country, p.sector, p.jobs, p.active_jobs, p.jobs_with_salary,
       round(p.median_salary::numeric) AS median_salary_per_year,
       CASE WHEN p.jobs_with_salary >= 15 THEN round(p.avg_pay_percentile::numeric, 1) END AS avg_pay_percentile,
       CASE WHEN p.jobs_with_salary >= 15 THEN round((p.median_salary / c.country_median)::numeric, 2) END AS salary_index,
       CASE WHEN p.jobs_extracted > 0 THEN round(100.0 * p.jobs_naming_a_tool / p.jobs_extracted, 1) END AS pct_naming_a_tool
FROM per_sector p JOIN per_country c ON c.country = p.country;

-- most requested named tools per sector (only jobs that went through the extraction)
CREATE OR REPLACE VIEW analytics.tool_demand AS
WITH extracted AS (
    SELECT country, sector, count(*) AS extracted_jobs FROM analytics.jobs WHERE tools IS NOT NULL GROUP BY 1, 2
)
SELECT j.country, j.sector, t.tool, count(*) AS jobs,
       round(100.0 * count(*) / x.extracted_jobs, 1) AS pct_of_sector_jobs
FROM analytics.jobs j
CROSS JOIN LATERAL unnest(j.tools) AS t(tool)
JOIN extracted x ON x.country = j.country AND x.sector IS NOT DISTINCT FROM j.sector
GROUP BY j.country, j.sector, t.tool, x.extracted_jobs;

-- seniority mix per sector (only jobs that went through the extraction)
CREATE OR REPLACE VIEW analytics.seniority_mix AS
SELECT country, sector, seniority, count(*) AS jobs,
       round(100.0 * count(*) / sum(count(*)) OVER (PARTITION BY country, sector), 1) AS pct
FROM analytics.jobs WHERE seniority IS NOT NULL GROUP BY country, sector, seniority;

CREATE OR REPLACE VIEW analytics.salary_by_seniority AS
SELECT country, seniority, count(salary_annual) AS jobs_with_salary,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY salary_annual))::numeric) AS median_salary_per_year,
       CASE WHEN count(salary_annual) >= 15 THEN round(avg(pay_percentile), 1) END AS avg_pay_percentile
FROM analytics.jobs WHERE seniority IS NOT NULL GROUP BY country, seniority;

CREATE OR REPLACE VIEW analytics.region_summary AS
SELECT country, region, count(*) AS jobs, count(*) FILTER (WHERE is_active) AS active_jobs,
       count(salary_annual) AS jobs_with_salary,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY salary_annual))::numeric) AS median_salary_per_year,
       CASE WHEN count(salary_annual) >= 15 THEN round(avg(pay_percentile), 1) END AS avg_pay_percentile
FROM analytics.jobs WHERE region IS NOT NULL GROUP BY country, region;

-- new postings per day on Tanitjobs, by the posting date shown on the site. Adzuna is deliberately
-- left out: we collect the newest 50 postings per category and day, so its daily counts measure our
-- collection, not the market. (first_seen is also unreliable for Tanitjobs before 2026-11-05.)
CREATE OR REPLACE VIEW analytics.daily_trend AS
SELECT source, country, date_posted AS day, count(*) AS new_jobs
FROM analytics.jobs WHERE source = 'tanitjobs' AND date_posted IS NOT NULL
GROUP BY source, country, date_posted;

-- Tunisian salary brackets: STATED (posting gives a bracket) next to ESTIMATED (salary-bracket model,
-- src.models.train_salary, for postings that are confidential or silent on pay). One row per sector
-- and bracket, plus 'All sectors'. Percentages are within the sector and within each group, so the
-- two distributions can be compared; pct_combined describes all postings of the sector.
-- Estimates are a model output (~62% exact, ~92% within one bracket): show them as estimates.
CREATE OR REPLACE VIEW analytics.salary_estimate_summary AS
WITH tn AS (
    SELECT j.sector,
           CASE WHEN j.salary_kind = 'bracket' THEN
               CASE WHEN f.salary_min < 1000 THEN '<1000' WHEN f.salary_min < 1500 THEN '1000-1500'
                    WHEN f.salary_min < 2000 THEN '1500-2000' ELSE '>=2000' END
           END AS stated_bracket,
           j.salary_bracket_estimated AS estimated_bracket,
           j.salary_estimate_confidence AS confidence
    FROM analytics.jobs j JOIN core.fact_job f ON f.job_pk = j.job_pk
    WHERE j.country = 'tn'
), long AS (
    SELECT sector, stated_bracket AS bracket, 'stated' AS kind, NULL::numeric AS confidence
    FROM tn WHERE stated_bracket IS NOT NULL
    UNION ALL
    SELECT sector, estimated_bracket, 'estimated', confidence
    FROM tn WHERE estimated_bracket IS NOT NULL AND stated_bracket IS NULL
), counted AS (
    SELECT CASE WHEN grouping(sector) = 1 THEN 'All sectors' ELSE coalesce(sector, 'Unknown') END AS sector,
           bracket,
           count(*) FILTER (WHERE kind = 'stated') AS stated_jobs,
           count(*) FILTER (WHERE kind = 'estimated') AS estimated_jobs,
           round(avg(confidence), 3) AS avg_estimate_confidence
    FROM long GROUP BY GROUPING SETS ((sector, bracket), (bracket))
)
SELECT sector, bracket, stated_jobs, estimated_jobs, avg_estimate_confidence,
       round(100.0 * stated_jobs / nullif(sum(stated_jobs) OVER s, 0), 1) AS pct_stated,
       round(100.0 * estimated_jobs / nullif(sum(estimated_jobs) OVER s, 0), 1) AS pct_estimated,
       round(100.0 * (stated_jobs + estimated_jobs) / nullif(sum(stated_jobs + estimated_jobs) OVER s, 0), 1) AS pct_combined,
       CASE bracket WHEN '<1000' THEN 1 WHEN '1000-1500' THEN 2 WHEN '1500-2000' THEN 3 ELSE 4 END AS bracket_order
FROM counted
WINDOW s AS (PARTITION BY sector);
