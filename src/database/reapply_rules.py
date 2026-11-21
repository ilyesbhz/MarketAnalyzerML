"""Re-apply the rule-based extraction (no language model, takes seconds) to every extracted job.

    python -m src.database.reapply_rules

Updates seniority, is_managerial, remote, tools and skills from the stored job texts using the
current rules (src/extraction/rules.py). Tools and skills only GROW (new dictionary entries are
added, nothing the language model found is removed). Safe to run as often as you like; the daily
pipeline runs it after the extraction.
"""
from collections import Counter

from src.database import db
from src.database.runlog import pipeline_step
from src.extraction.rules import (detect_methods, detect_remote, detect_tools, is_managerial,
                                  rule_seniority)

SELECT = """
SELECT e.job_pk, f.title, f.description, e.seniority, e.remote, e.tools, e.skills, e.is_managerial
FROM core.job_extraction e JOIN core.fact_job f USING (job_pk)
"""
UPDATE = ("UPDATE core.job_extraction SET seniority = %s, is_managerial = %s, remote = %s, tools = %s, skills = %s "
          "WHERE job_pk = %s")


def union(old, new):
    out, seen = [], set()
    for x in list(old or []) + list(new or []):
        if x and x.lower() not in seen:
            seen.add(x.lower())
            out.append(x)
    return out


def main():
    with pipeline_step("apply_rules") as run:
        with db.connect() as conn:
            conn.execute("ALTER TABLE core.job_extraction ADD COLUMN IF NOT EXISTS is_managerial BOOLEAN")
            rows = conn.execute(SELECT).fetchall()
            run["rows_in"] = len(rows)
            before, after, changed, updates = Counter(), Counter(), 0, []
            for job_pk, title, description, sen, rem, tools, skills, mgr in rows:
                text = f"{title}\n{description or ''}"
                new_sen = rule_seniority(title, description)
                new_mgr = is_managerial(title)
                new_rem = detect_remote(text)
                new_tools = union(tools, detect_tools(text))
                new_skills = union(skills, detect_methods(text))
                before[sen] += 1
                after[new_sen] += 1
                if (new_sen, new_mgr, new_rem, new_tools, new_skills) != (sen, mgr, rem, list(tools or []), list(skills or [])):
                    changed += 1
                    updates.append((new_sen, new_mgr, new_rem, new_tools, new_skills, job_pk))
            conn.cursor().executemany(UPDATE, updates)
            run["rows_out"] = changed
    print(f"Jobs checked: {len(rows)} | updated: {changed}")
    print("Seniority before:", dict(before.most_common()))
    print("Seniority after: ", dict(after.most_common()))
    print("Managerial titles:", sum(1 for r in rows if is_managerial(r[1])))


if __name__ == "__main__":
    main()
