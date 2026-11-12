"""
Applicant Profile Service
==========================
Reads and writes the candidate "answer sheet" (Apply/Apply-2/details.xlsx)
used by the auto-apply bot to fill required fields on job portal
application forms — as plain JSON, so the web UI can offer a friendly form
instead of requiring people to hand-edit a spreadsheet.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List
import json
import re

from openpyxl import Workbook, load_workbook

from app.config.required_fields import ALL_FIELDS, DEFAULT_PROFILE_KEYS

APP_ROOT = Path(__file__).resolve().parent.parent.parent
DETAILS_FILE = APP_ROOT / "Apply" / "Apply-2" / "details.xlsx"
QUESTION_BANK_FILE = APP_ROOT / "data" / "profile" / "question_bank.json"


def normalize_question(question: str) -> str:
    return re.sub(r"\s+", " ", str(question or "").strip().lower())


def load_question_bank() -> List[Dict]:
    if not QUESTION_BANK_FILE.exists():
        return []
    try:
        data = json.loads(QUESTION_BANK_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_question_answer(question: str, answer: str, source: str = "naukri") -> Dict:
    normalized = normalize_question(question)
    if not normalized:
        return {}
    entries = load_question_bank()
    now = datetime.now().isoformat(timespec="seconds")
    entry = next((item for item in entries if item.get("normalized") == normalized), None)
    if entry is None:
        entry = {"id": normalized, "question": str(question).strip(), "normalized": normalized,
                 "answer": str(answer or "").strip(), "source": source, "count": 0, "first_seen": now}
        entries.append(entry)
    elif answer is not None and str(answer).strip():
        entry["answer"] = str(answer).strip()
    entry["count"] = int(entry.get("count") or 0) + 1
    entry["last_seen"] = now
    entry["source"] = source or entry.get("source", "naukri")
    entries.sort(key=lambda item: item.get("last_seen", ""), reverse=True)
    QUESTION_BANK_FILE.parent.mkdir(parents=True, exist_ok=True)
    QUESTION_BANK_FILE.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    return entry


def update_question_answer(question_id: str, answer: str) -> Dict | None:
    entries = load_question_bank()
    entry = next((item for item in entries if item.get("id") == question_id), None)
    if entry is None:
        return None
    entry["answer"] = str(answer or "").strip()
    entry["updated_at"] = datetime.now().isoformat(timespec="seconds")
    QUESTION_BANK_FILE.parent.mkdir(parents=True, exist_ok=True)
    QUESTION_BANK_FILE.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    return entry


def load_profile() -> Dict[str, str]:
    """Return the current applicant profile as a flat dict of key -> value."""
    profile = {key: "" for key in DEFAULT_PROFILE_KEYS}

    if not DETAILS_FILE.exists():
        return profile

    try:
        wb = load_workbook(DETAILS_FILE)
        ws = wb.active
        headers = [str(c.value).strip().lower().replace(" ", "_") for c in ws[1] if c.value]
        row = list(ws.iter_rows(min_row=2, max_row=2, values_only=True))
        if row:
            values = row[0]
            for header, value in zip(headers, values):
                profile[header] = "" if value is None else str(value).strip()
    except Exception:
        pass

    return profile


def save_profile(data: Dict[str, str]) -> Dict[str, str]:
    """Merge `data` into the existing profile and persist it back to
    details.xlsx (creating the file if it doesn't exist yet)."""
    profile = load_profile()

    for key, value in (data or {}).items():
        key = str(key).strip().lower().replace(" ", "_")
        profile[key] = "" if value is None else str(value).strip()

    # Ensure every column the bot might look for still exists, even if blank
    for key in DEFAULT_PROFILE_KEYS:
        profile.setdefault(key, "")

    DETAILS_FILE.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Profile"

    headers = list(profile.keys())
    ws.append(headers)
    ws.append([profile[h] for h in headers])
    wb.save(DETAILS_FILE)

    return profile


def profile_completeness(profile: Dict[str, str] = None) -> Dict:
    profile = profile if profile is not None else load_profile()
    filled = [k for k in ALL_FIELDS if str(profile.get(k, "") or "").strip()]
    total = len(ALL_FIELDS)
    return {
        "filled": len(filled),
        "total": total,
        "percent": round(100 * len(filled) / total, 1) if total else 0,
        "missing_keys": [k for k in ALL_FIELDS if k not in filled],
    }
