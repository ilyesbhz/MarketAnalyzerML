"""Rule-based extraction (no LLM): remote work, seniority evidence, named tools.

Rules decide where the text gives explicit evidence; the LLM only adds what the
rules cannot find (see llm_extract.py).
"""
import re
import unicodedata


def fold(s):
    """Lower-case and strip accents so patterns are simple."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


# ---------------------------------------------------------------- remote work
HYBRID = re.compile(r"hybrid|hybride|t[ée]l[ée]travail|travail [àa] domicile|work from home|\bwfh\b", re.I)
REMOTE = re.compile(
    r"(100\s?%|full|fully|enti[èe]rement|totalement|compl[èe]t)\W{0,3}(remote|t[ée]l[ée]travail|[àa] distance)"
    r"|remote only|fully remote|full remote|poste (en )?remote|travail (enti[èe]rement )?[àa] distance",
    re.I)
ONSITE = re.compile(r"in[- ]office|on[- ]?site|sur site|pr[ée]sentiel|pr[ée]sence (physique )?(sur site|obligatoire)", re.I)


def detect_remote(text):
    """Return 'remote', 'hybrid', 'onsite' or 'unspecified'."""
    text = text or ""
    if REMOTE.search(text):
        return "remote"
    if HYBRID.search(text):
        return "hybrid"
    if ONSITE.search(text):
        return "onsite"
    return "unspecified"


# ------------------------------------------------------------------ seniority
INTERN_TITLE = re.compile(r"\b(stage|stagiaire|pfe|alternance|alternant|internship|intern)\b")
INTERN_TEXT = re.compile(r"\b(pfe|alternance|internship|stage de fin d'etudes?)\b")
MANAGER_TITLE = re.compile(r"\b(responsables?|chefs?|directeurs?|directrices?|managers?|leads?|superviseu[rs]es?|"
                           r"superviseurs?|head of|gerant[es]*)\b")
# job names that contain a management word but are not a management level
NOT_MANAGER = re.compile(r"\b(chef de (rang|partie)|community manager|account manager|social media manager)\b")
SENIOR_STRONG = re.compile(r"\b(senior|confirme[es]*)\b")
EXPERIENCED = re.compile(r"\bexperimente[es]*\b")      # weak: counts only in the title
JUNIOR = re.compile(r"\b(debutant[es]*|jeunes? diplome[es]*|junior|sans experience|entry[- ]level|"
                    r"nouveaux? diplome[es]*)\b")
YEARS = re.compile(r"(\d{1,2})(?:\s*(?:-|to)\s*(\d{1,2})|\s+a\s+(\d{1,2}))?\s*\+?\s*(?:ans?|annees?|years?)\b")
EXP_CONTEXT = re.compile(r"exp|minimum|au moins|at least|\bmin\b")


def years_of_experience(text):
    """First 'N ans' / 'N a M ans' near the word experience/minimum -> number (range = average)."""
    for m in YEARS.finditer(text):
        ctx = text[max(0, m.start() - 45): m.end() + 45]
        if EXP_CONTEXT.search(ctx):
            lo = int(m.group(1))
            hi = m.group(2) or m.group(3)
            return (lo + int(hi)) / 2 if hi else lo
    return None


def rule_seniority(title, description):
    """intern / junior / mid / senior / manager / unspecified, from explicit evidence only."""
    t, d = fold(title), fold(description)
    both = f"{t}\n{d}"
    if INTERN_TITLE.search(t):
        return "intern"
    if MANAGER_TITLE.search(t) and not NOT_MANAGER.search(t):
        return "manager"
    if INTERN_TEXT.search(both):
        return "intern"
    if SENIOR_STRONG.search(t) or EXPERIENCED.search(t):
        return "senior"
    has_junior = bool(JUNIOR.search(both))
    has_senior = bool(SENIOR_STRONG.search(d))
    if has_junior and (has_senior or EXPERIENCED.search(d)):
        return "unspecified"            # the posting asks for both beginners and experienced people
    if has_senior:
        return "senior"
    years = years_of_experience(both)
    if years is not None:
        return "junior" if years < 2 else "mid" if years < 5 else "senior"
    return "junior" if has_junior else "unspecified"


def combine_seniority(llm, rule):
    """Final seniority = the rule result.

    Measured on the development set: the LLM alone scored 78% against 100% for the rules,
    and a stricter prompt did not change that, because the model keeps guessing the level from
    the kind of job. The LLM's own answer is still stored (seniority_llm) for comparison."""
    return rule


# ---------------------------------------------------------------------- tools
# distinctive names: matched case-insensitively
_CI = [
    "Photoshop", "Illustrator", "InDesign", "Premiere Pro", "After Effects", "DaVinci Resolve", "Final Cut Pro",
    "CapCut", "Canva", "Figma", "Lightroom", "Blender", "AutoCAD", "SolidWorks", "CATIA", "Revit", "ArchiCAD",
    "SketchUp", "Lumion", "MS Project", "Microsoft Project", "Primavera", "ETABS", "Fusion 360", "ANSYS", "MATLAB",
    "Odoo", "Salesforce", "HubSpot", "Zoho", "Pipedrive", "QuickBooks", "Dynamics 365", "Navision", "Power BI",
    "Tableau", "Qlik", "Talend", "SSIS", "SSRS", "SQL Server", "MySQL", "PostgreSQL", "MongoDB", "Python",
    "JavaScript", "TypeScript", "React", "Angular", "Node.js", "PHP", "Laravel", "Spring Boot", "Docker",
    "Kubernetes", "Linux", "Windows Server", "Active Directory", "Azure", "AWS", "Airflow", "Spark", "Hadoop",
    "Kafka", "VBA", "Cisco", "Fortinet", "VMware", "GLPI", "Zabbix", "Selenium", "Jenkins", "Terraform",
    "Ansible", "Flutter", "WordPress", "Shopify", "WooCommerce", "Mailchimp", "Google Ads", "Meta Ads",
    "Facebook Ads", "Google Analytics", "SEMrush", "Hootsuite", "Google Workspace", "Microsoft 365",
    "Office 365", "Pack Office", "Microsoft Office", "PowerPoint", "Outlook", "Jira", "Trello", "Slack",
    "Notion", "SQL", "Java", ".NET", "C#", "C++", "Microsoft Access", "MS Access", "Microsoft Teams", "MS Word",
    "Microsoft Word", "MS Excel", "Microsoft Excel",
]
# ambiguous with ordinary words: matched case-sensitively
_CS = ["Word", "Sage", "SAP", "Git", "Oracle"]

ALIASES = {"pack office": "Microsoft Office", "office 365": "Microsoft Office", "microsoft 365": "Microsoft Office",
           "ms project": "Microsoft Project", "ms access": "Microsoft Access", "ms word": "Word",
           "microsoft word": "Word", "ms excel": "Excel", "microsoft excel": "Excel"}


def _pat(name, flags):
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", flags)


_TOOL_PATTERNS = ([(n, _pat(n, re.I)) for n in _CI] + [(n, _pat(n, 0)) for n in _CS]
                  + [("Excel", re.compile(r"(?<![A-Za-z0-9_])[Ee]xcel(?![A-Za-z0-9_])(?!\s+(?:at|in|as|on)\b)"))])


def detect_tools(text):
    """Named tools found in the text, in order of appearance."""
    text = text or ""
    found = []
    for name, pat in _TOOL_PATTERNS:
        m = pat.search(text)
        if m:
            found.append((m.start(), ALIASES.get(name.lower(), name)))
    out, seen = [], set()
    for _, name in sorted(found):
        if name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out
