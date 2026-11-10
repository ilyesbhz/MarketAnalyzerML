import json

with open("jobs_test.json", encoding="utf-8") as f:
    jobs = json.load(f)

print("=== INFO BOX VALUES ===")
for j in jobs:
    ib = j["info_box"]
    print(j["job_id"], "|", j["title"][:35])
    for k in ["Experience", "Langue", "Niveau d'étude", "Rémunération proposée"]:
        print("    ", k, "->", ib.get(k))

print("\n=== DATES (posted / valid_through / sitemap lastmod) ===")
for j in sorted(jobs, key=lambda x: x["date_posted"]):
    print(j["job_id"], j["date_posted"][:10], j["valid_through"][:10], j["lastmod"])

print("\n=== ODD CASES ===")
for j in jobs:
    if not j["region"] or j["applicants"] is None or len(j["description"]) < 100:
        print(j["job_id"], "| region:", repr(j["region"]), "| city:", repr(j["city"]),
              "| applicants:", j["applicants"], "| desc_len:", len(j["description"]))
        print("    desc:", repr(j["description"][:150]))