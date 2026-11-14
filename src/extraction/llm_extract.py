"""Extract seniority, tools, and skills with Ollama and deterministic rules."""
import argparse
import json
import statistics

import pandas as pd
import requests

import config
from src.evaluation.score_gold import load_gold, predictions_path
import re

from src.extraction.rules import (combine_seniority, detect_remote, detect_tools,
                                  rule_seniority)

SENIORITY = ["intern", "junior", "mid", "senior", "manager", "unspecified"]
MAX_CHARS = 3500          # description is cut here to keep the prompt small
FULL_RUN_JOBS = 3128      # usable Tanitjobs jobs, for the time estimate

SCHEMA = {
    "type": "object",
    "properties": {
        "seniority": {"type": "string", "enum": SENIORITY},
        "tools": {"type": "array", "items": {"type": "string"}},
        "skills": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["seniority", "tools", "skills"],
}

SYSTEM_PROMPT = """You extract structured data from ONE job posting (French, English or Arabic).
Answer only with JSON. Use only what the text says; never guess.

seniority: exactly one of intern, junior, mid, senior, manager, unspecified.
Decide ONLY from level words or years of experience written in the posting or its title.
The kind of job does NOT tell the level: assistant, aide, conseiller, technicien, vendeur are NOT junior by themselves.
- intern: stage, PFE, alternance, internship.
- junior: debutant, jeune diplome, entry level, or LESS than 2 years of experience.
- mid: 2 to under 5 years of experience ("2 ans minimum" is mid), with no senior or manager wording.
- senior: "senior", "confirme", "experimente", or 5 or more years.
- manager: responsable, chef de, lead, directeur, superviseur, manager. This wins over senior.
- unspecified: no level word and no number of years. This is the most common answer.

tools: names of SOFTWARE or SYSTEMS written in the text (for example Excel, SAP, Photoshop, AutoCAD). Not machines, not tasks, not skills.
Never list generic categories or phrases: software, hardware, ERP, CRM, "outils bureautiques", "internal software".
Empty list if none.

skills: up to 8 short skills the job requires (2-4 words, lowercase, same language as the posting).
Empty list if none."""

# generic words the model sometimes returns as "tools"
GENERIC_TOOLS = {"erp", "crm", "software", "hardware", "logiciel", "logiciels", "internal software",
                 "outils bureautiques", "bureautique", "office", "gestion financiere", "outils", "systeme"}


def filter_llm_tools(tools, text):
    """Keep an LLM tool only if it is not generic, appears in the text, and is written as a
    proper name there (capitalized). Returns the spelling used in the text."""
    out = []
    for t in tools:
        if re.sub(r"[^a-z ]", "", t.lower().replace("\u00e8", "e").replace("\u00e9", "e")) in GENERIC_TOOLS:
            continue
        m = re.search(r"(?<![A-Za-z0-9])" + re.escape(t) + r"(?![A-Za-z0-9])", text or "", re.I)
        if m and (m.group(0)[0].isupper() or m.group(0)[0].isdigit()):
            out.append(m.group(0))
    return out


def merge_tools(*lists):
    """Union, case-insensitive; if one name contains another keep the longer."""
    out = []
    for lst in lists:
        for t in lst:
            low = t.lower()
            if any(low == o.lower() or low in o.lower() for o in out):
                continue
            out = [o for o in out if o.lower() not in low] + [t]
    return out


def clean_list(items, max_items, max_len=60):
    out, seen = [], set()
    for x in items or []:
        if not isinstance(x, str):
            continue
        x = " ".join(x.split()).strip(" .,;:-")
        if not x or len(x) > max_len or x.lower() in seen:
            continue
        seen.add(x.lower())
        out.append(x)
        if len(out) >= max_items:
            break
    return out


def parse_answer(text):
    """JSON text from the model -> clean dict, or None if unusable."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    sen = str(data.get("seniority", "")).strip().lower()
    if sen not in SENIORITY:
        sen = "unspecified"
    return {"seniority": sen,
            "tools": clean_list(data.get("tools"), 15),
            "skills": clean_list(data.get("skills"), 8)}


def ask(title, description, model=None, base_url=None):
    """One Ollama call. Returns (result dict or None, stats dict)."""
    base_url = base_url or config.OLLAMA_URL
    body = {
        "model": model or config.OLLAMA_MODEL, "stream": False, "keep_alive": "30m",
        "format": SCHEMA, "options": {"temperature": 0, "num_ctx": 4096},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Title: {title}\n\nDescription:\n{(description or '')[:MAX_CHARS]}"},
        ],
    }
    r = requests.post(f"{base_url}/api/chat", json=body, timeout=300)
    if r.status_code == 400:           # older Ollama without JSON-schema support
        body["format"] = "json"
        r = requests.post(f"{base_url}/api/chat", json=body, timeout=300)
    r.raise_for_status()
    data = r.json()
    ev = data.get("eval_duration") or 0
    stats = {
        "seconds": (data.get("total_duration") or 0) / 1e9,
        "prompt_tokens": data.get("prompt_eval_count"),
        "output_tokens": data.get("eval_count"),
        "tokens_per_s": (data.get("eval_count") or 0) / (ev / 1e9) if ev else None,
    }
    return parse_answer((data.get("message") or {}).get("content")), stats


def extract_job(title, description, **kw):
    """Full extraction for one job: LLM + rules, combined.
    Returns (result, stats, parse_failed)."""
    result, stats = ask(title, description, **kw)
    failed = result is None
    if failed:                          # one retry, then fall back to empty values
        result, stats2 = ask(title, description, **kw)
        stats["seconds"] += stats2["seconds"]
        failed = result is None
    result = result or {"seniority": "unspecified", "tools": [], "skills": []}
    text = f"{title}\n{description or ''}"
    rule_sen = rule_seniority(title, description)
    rule_tools = detect_tools(text)
    out = {
        "seniority_llm": result["seniority"],
        "seniority_rules": rule_sen,
        "seniority": combine_seniority(result["seniority"], rule_sen),
        "tools_llm": result["tools"],
        "tools_rules": rule_tools,
        "tools": merge_tools(rule_tools, filter_llm_tools(result["tools"], text)),
        "skills": result["skills"],
        "remote": detect_remote(text),
    }
    return out, stats, failed


def run_gold(limit=None, prefix="gold"):
    gold, src = load_gold(prefix)
    jobs = pd.read_csv(config.JOBS_CLEAN_CSV, dtype={"job_id": str},
                       usecols=["job_id", "title", "description"])
    df = gold[["job_id"]].merge(jobs, on="job_id", how="left")
    if limit:
        df = df.head(limit)
    print(f"Set: {prefix} | model: {config.OLLAMA_MODEL} | jobs: {len(df)} | labels: {src}\n")

    rows, secs = [], []
    for i, r in enumerate(df.itertuples(), 1):
        result, st, failed = extract_job(r.title, r.description)
        secs.append(st["seconds"])
        tps = f"{st['tokens_per_s']:.0f} tok/s" if st["tokens_per_s"] else "?"
        print(f"[{i}/{len(df)}] {st['seconds']:5.1f}s | {result['seniority']:<11} (llm {result['seniority_llm']}, rules {result['seniority_rules']}) | "
              f"tools: {', '.join(result['tools']) or '-'} | {st['prompt_tokens']} in, "
              f"{st['output_tokens']} out, {tps}{' | PARSE FAILED' if failed else ''}")
        rows.append({"job_id": r.job_id, "title": r.title,
                     "seniority_llm": result["seniority_llm"], "seniority_rules": result["seniority_rules"],
                     "seniority_pred": result["seniority"],
                     "tools_llm": "|".join(result["tools_llm"]), "tools_rules": "|".join(result["tools_rules"]),
                     "tools_pred": "|".join(result["tools"]), "skills_pred": "|".join(result["skills"]),
                     "remote_pred": result["remote"], "parse_failed": failed,
                     "seconds": round(st["seconds"], 2), "prompt_tokens": st["prompt_tokens"],
                     "output_tokens": st["output_tokens"], "model": config.OLLAMA_MODEL})

    config.EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)
    out = predictions_path(prefix)
    pd.DataFrame(rows).to_csv(out, index=False, encoding="utf-8-sig")
    steady = secs[1:] or secs           # the first call also loads the model
    avg = statistics.mean(steady)
    print(f"\nSaved {len(rows)} predictions to {out}")
    print(f"First call (includes model loading): {secs[0]:.1f}s")
    print(f"Average per job afterwards: {avg:.1f}s (median {statistics.median(steady):.1f}s)")
    print(f"Estimated time for {FULL_RUN_JOBS} jobs: {avg * FULL_RUN_JOBS / 3600:.1f} hours")
    print(f"Parse failures: {sum(r['parse_failed'] for r in rows)}")
    print(f"\nNext: python -m src.evaluation.score_gold --set {prefix}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--gold", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--set", default="gold", choices=["gold", "test"])
    args = ap.parse_args()
    try:
        if args.check:
            result, st = ask("Responsable comptable", "Maitrise de SAP et Excel. 5 ans d'experience.")
            print("Ollama answered in", round(st["seconds"], 1), "s ->", result)
        elif args.gold:
            run_gold(args.limit, args.set)
        else:
            ap.print_help()
    except requests.ConnectionError:
        raise SystemExit(f"Cannot reach Ollama at {config.OLLAMA_URL}. Is the Ollama app running?")
    except requests.HTTPError as e:
        raise SystemExit(f"Ollama error: {e}. If the model is missing run: ollama pull {config.OLLAMA_MODEL}")


if __name__ == "__main__":
    main()
