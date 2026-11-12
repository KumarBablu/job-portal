"""
Required-Fields Catalog
========================
Single source of truth describing every answer the auto-apply bot can fill
into a job-portal application form (Naukri / LinkedIn / Indeed / Glassdoor /
company career sites), grouped for the "Applicant Profile" UI.

Each entry's `keywords` mirror the FIELD_MAP question-matching keywords used
by the auto-apply bot (Apply/Apply-2/app.py), so the web UI can tell the user
*before* they hit "Auto Apply" which fields a job's application form is
likely to ask for, and whether the profile currently has an answer saved.

This module has no external dependencies (no AI service, no Ollama) — it is
pure Python configuration + small helper functions.
"""
from __future__ import annotations

from typing import Dict, List

# ──────────────────────────────────────────────────────────────────────────
# FIELD CATALOG  (key -> metadata)
# ──────────────────────────────────────────────────────────────────────────
# key matches the column name used in details.xlsx / the profile JSON.

FIELD_GROUPS: List[Dict] = [
    {
        "group": "Identity",
        "icon": "user",
        "fields": [
            {"key": "name", "label": "Full Name", "type": "text", "keywords": ["full name", "your name", "name"]},
            {"key": "first_name", "label": "First Name", "type": "text", "keywords": ["first name"]},
            {"key": "last_name", "label": "Last Name", "type": "text", "keywords": ["last name", "surname", "family name"]},
            {"key": "email", "label": "Email", "type": "email", "keywords": ["email", "e-mail"]},
            {"key": "phone", "label": "Phone", "type": "text", "keywords": ["phone", "mobile", "contact number", "phone number"]},
            {"key": "password", "label": "Portal Password", "type": "password", "keywords": [], "sensitive": True},
        ],
    },
    {
        "group": "Location",
        "icon": "map-pin",
        "fields": [
            {"key": "current_location", "label": "Current Location", "type": "text", "keywords": ["current location", "current city", "where are you based"]},
            {"key": "preferred_location", "label": "Preferred Location(s)", "type": "text", "keywords": ["preferred location", "preferred city", "willing to relocate"]},
            {"key": "country", "label": "Country", "type": "text", "keywords": ["country"]},
            {"key": "zip_code", "label": "ZIP / Postal Code", "type": "text", "keywords": ["zip", "postal", "pincode"]},
        ],
    },
    {
        "group": "Compensation",
        "icon": "wallet",
        "fields": [
            {"key": "current_ctc", "label": "Current CTC", "type": "text", "keywords": ["current ctc", "current salary", "current package"]},
            {"key": "expected_ctc", "label": "Expected CTC", "type": "text", "keywords": ["expected ctc", "expected salary", "salary expectation"]},
            {"key": "notice_period", "label": "Notice Period", "type": "text", "keywords": ["notice period", "notice"]},
        ],
    },
    {
        "group": "Experience",
        "icon": "briefcase",
        "fields": [
            {"key": "total_experience", "label": "Total Experience (yrs)", "type": "number", "keywords": ["total experience", "years of exp", "how many years"]},
            {"key": "relevant_experience", "label": "Relevant Experience (yrs)", "type": "number", "keywords": ["relevant experience", "relevant exp"]},
            {"key": "current_employer", "label": "Current Employer", "type": "text", "keywords": ["employer", "current company", "where do you work"]},
            {"key": "highest_qualification", "label": "Highest Qualification", "type": "text", "keywords": ["qualification", "education", "degree"]},
            {"key": "work_authorization", "label": "Work Authorization", "type": "text", "keywords": ["visa", "work authoriz", "eligible to work", "sponsorship"]},
        ],
    },
    {
        "group": "Skills",
        "icon": "code",
        "fields": [
            {"key": "skills", "label": "Key Skills (comma separated)", "type": "text", "keywords": ["skill"]},
            {"key": "python_experience", "label": "Python Experience (yrs)", "type": "text", "keywords": ["python"]},
            {"key": "sql_experience", "label": "SQL Experience (yrs)", "type": "text", "keywords": ["sql"]},
            {"key": "excel_experience", "label": "Excel Experience (yrs)", "type": "text", "keywords": ["excel"]},
            {"key": "powerbi_experience", "label": "Power BI Experience (yrs)", "type": "text", "keywords": ["power bi", "powerbi"]},
            {"key": "tableau_experience", "label": "Tableau Experience (yrs)", "type": "text", "keywords": ["tableau"]},
            {"key": "r_experience", "label": "R Experience (yrs)", "type": "text", "keywords": ["r programming"]},
        ],
    },
    {
        "group": "Links",
        "icon": "link",
        "fields": [
            {"key": "linkedin_url", "label": "LinkedIn URL", "type": "text", "keywords": ["linkedin", "profile url"]},
            {"key": "github_url", "label": "GitHub URL", "type": "text", "keywords": ["github"]},
            {"key": "portfolio_url", "label": "Portfolio / Website", "type": "text", "keywords": ["portfolio", "website", "personal url"]},
            {"key": "resume_path", "label": "Resume File Path", "type": "text", "keywords": []},
        ],
    },
    {
        "group": "Documents",
        "icon": "file-text",
        "fields": [
            {"key": "cover_letter", "label": "Default Cover Letter Snippet", "type": "textarea", "keywords": ["cover letter"]},
        ],
    },
    {
        "group": "EEO / Compliance",
        "icon": "shield",
        "fields": [
            {"key": "gender", "label": "Gender", "type": "text", "keywords": ["gender"]},
            {"key": "veteran_status", "label": "Veteran Status", "type": "text", "keywords": ["veteran"]},
            {"key": "disability_status", "label": "Disability Status", "type": "text", "keywords": ["disability", "disabled"]},
            {"key": "ethnicity", "label": "Ethnicity", "type": "text", "keywords": ["ethnicity", "race", "hispanic"]},
        ],
    },
]

# Flat lookup: key -> field meta
ALL_FIELDS: Dict[str, Dict] = {
    f["key"]: {**f, "group": g["group"]}
    for g in FIELD_GROUPS
    for f in g["fields"]
}

DEFAULT_PROFILE_KEYS = list(ALL_FIELDS.keys())


def detect_required_fields(text: str) -> List[str]:
    """Given a job title/description, return the list of field keys whose
    keywords appear in the text — i.e. fields the application form is
    likely to ask for."""
    text = (text or "").lower()
    hits: List[str] = []
    for key, meta in ALL_FIELDS.items():
        for kw in meta.get("keywords", []):
            if kw and kw in text:
                hits.append(key)
                break
    return hits


def field_coverage(profile: Dict, field_keys: List[str]) -> Dict:
    """Split field_keys into filled vs missing based on the given profile dict."""
    filled, missing = [], []
    for key in field_keys:
        val = str(profile.get(key, "") or "").strip()
        (filled if val else missing).append(key)
    return {"filled": filled, "missing": missing}
