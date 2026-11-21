"""Import the exported jobs.json into the web app's database (runs `npm run import:jobs` there).

    python -m src.export.import_app

The app folder is the parent of APP_DATA_DIR (.env), e.g. APP_DATA_DIR=...\\job-analyzer\\data
-> ...\\job-analyzer. Without APP_DATA_DIR there is no app to update: the step says so and succeeds.

Exit code 1 when npm fails, or when the import reports that too many records were invalid (its
safety net then leaves old offers active), so the problem shows up in ops.pipeline_run.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

import config

load_dotenv(config.ROOT / ".env")
SAFETY_NET_WARNING = "Too many invalid records"


def main():
    data_dir = os.getenv("APP_DATA_DIR")
    if not data_dir:
        print("APP_DATA_DIR is not set in .env: no web app to import into, step skipped.")
        return
    app_dir = Path(data_dir).parent
    if not (app_dir / "package.json").exists() or not (Path(data_dir) / "jobs.json").exists():
        sys.exit(f"Expected the web app in {app_dir} (package.json) with {data_dir}\\jobs.json")
    npm = shutil.which("npm")                # npm.cmd on Windows
    if not npm:
        sys.exit("npm was not found on PATH: install Node.js or add it to PATH")

    print(f"Importing {Path(data_dir) / 'jobs.json'} into the app ({app_dir})")
    # Prisma prefers an existing DATABASE_URL environment variable over the app's own .env: drop
    # this project's one (loaded from tn-jobs/.env), or the import would target the wrong database
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    proc = subprocess.Popen([npm, "run", "import:jobs"], cwd=app_dir, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    warned = False
    for line in proc.stdout:
        print(line.rstrip(), flush=True)
        warned = warned or SAFETY_NET_WARNING in line
    code = proc.wait()
    if code:
        sys.exit(f"npm run import:jobs failed (exit code {code})")
    if warned:
        sys.exit("Import finished, but too many records were invalid: old offers were NOT deactivated")


if __name__ == "__main__":
    main()
