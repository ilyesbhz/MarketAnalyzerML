@echo off
REM Reorganizes the tn-jobs folder. Run it ONCE, from the tn-jobs folder, after making a backup copy.
cd /d "%~dp0"

if not exist data mkdir data
if exist raw if not exist data\raw move raw data\raw
for %%d in (data\interim data\processed data\gold data\extracted exploration exploration\old_versions docs notebooks) do if not exist %%d mkdir %%d

REM --- data files ---
if exist job_urls.json   move job_urls.json   data\interim\
if exist gone.log        move gone.log        data\interim\
if exist jobs_clean.csv  move jobs_clean.csv  data\processed\
if exist jobs_clean.json move jobs_clean.json data\processed\
if exist gold_sample.csv          move gold_sample.csv          data\gold\
if exist gold_sample_labeled.xlsx move gold_sample_labeled.xlsx data\gold\
if exist gold_labels.csv          move gold_labels.csv          data\gold\

REM --- one-off investigation scripts and files ---
for %%f in (check_sitemap.py fetch_one.py scrape_test.py inspect_test.py diagnose_missing.py sample_job.html sitemap_index.xml jobs_test.json) do if exist %%f move %%f exploration\

REM --- old versions of the pipeline scripts (replaced by the ones in src\) ---
for %%f in (scrape_all.py parse_job.py parse_all.py make_gold_sample.py) do if exist %%f move %%f exploration\old_versions\

echo.
echo Done. Now check the structure with:  tree /F /A  (or  dir )
