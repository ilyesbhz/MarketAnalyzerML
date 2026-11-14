"""Map source-specific categories to unified sectors."""
import csv
import re

import config

REFERENCE_DIR = config.DATA / "reference"


def _norm(s):
    s = (s or "").replace("\u2019", "'").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip().lower()


def _load(filename):
    with open(REFERENCE_DIR / filename, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))[1:]
    return {_norm(k): v for k, v in rows}


_TAN = _load("sector_map_tanitjobs.csv")
_ADZ = _load("sector_map_adzuna.csv")


def tanitjobs_sectors(categories):
    """'Comptabilité|Finance' -> ['Finance & Accounting'] (unique, order kept; unknown -> [])."""
    out = []
    for c in (categories or "").split("|"):
        s = _TAN.get(_norm(c))
        if s and s not in out:
            out.append(s)
    return out


def adzuna_sector(tag):
    return _ADZ.get(_norm(tag), "Other / General")
