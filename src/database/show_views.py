"""Print a few rows of every analytics view, to check them.   python -m src.database.show_views"""
from src.database import db
from src.database.summary import show

QUERIES = [
    ("market_overview", "SELECT * FROM analytics.market_overview ORDER BY country"),
    ("sector_summary (tn, ranked by pay percentile; 50 = typical pay in the country)",
     "SELECT * FROM analytics.sector_summary WHERE country = 'tn' AND avg_pay_percentile IS NOT NULL "
     "ORDER BY avg_pay_percentile DESC LIMIT 10"),
    ("pay percentile by sector, three countries side by side (sectors with enough stated salaries)",
     "SELECT sector, max(avg_pay_percentile) FILTER (WHERE country = 'tn') AS tn, "
     "max(avg_pay_percentile) FILTER (WHERE country = 'fr') AS fr, "
     "max(avg_pay_percentile) FILTER (WHERE country = 'gb') AS gb "
     "FROM analytics.sector_summary GROUP BY sector ORDER BY tn DESC NULLS LAST LIMIT 12"),
    ("tool_demand (top 10, tn)", "SELECT * FROM analytics.tool_demand WHERE country = 'tn' ORDER BY jobs DESC LIMIT 10"),
    ("seniority_mix (IT sector, tn)", "SELECT * FROM analytics.seniority_mix WHERE country = 'tn' AND sector LIKE 'IT%' ORDER BY jobs DESC"),
    ("salary_by_seniority", "SELECT * FROM analytics.salary_by_seniority ORDER BY country, avg_pay_percentile NULLS LAST"),
    ("region_summary (top 6 by jobs, tn)", "SELECT * FROM analytics.region_summary WHERE country = 'tn' ORDER BY jobs DESC LIMIT 6"),
    ("daily_trend (Tanitjobs, last 8 posting days)", "SELECT * FROM analytics.daily_trend ORDER BY day DESC LIMIT 8"),
    ("oldest postings still in the data (age in days)",
     "SELECT country, max(age_days) AS oldest_days, count(*) FILTER (WHERE age_days > 90) AS older_than_90_days "
     "FROM analytics.jobs GROUP BY country ORDER BY country"),
]


def main():
    with db.connect() as conn:
        for title, q in QUERIES:
            show(title, conn.execute(q))


if __name__ == "__main__":
    main()
