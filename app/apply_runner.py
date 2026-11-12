"""
Auto-apply integration for the main job agent.

Builds an Apply/Apply-2 jobs spreadsheet from scraped jobs, runs the existing
auto-apply bot in a background thread, and syncs results back into the main
jobs JSON and SQLite database.
"""

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook

from app.database.db import SessionLocal
from app.models.job_model import Job
from app.services.job_service import upsert_job, update_job

STATE = {
    "running": False,
    "log": [],
    "last_run": None,
    "summary": {},
    "results": [],
}

APP_ROOT = Path(__file__).resolve().parent.parent
APPLY_ROOT = APP_ROOT / "Apply" / "Apply-2"
DATA_JOBS_FILE = APP_ROOT / "data" / "jobs" / "jobs_data.json"
APPLY_JOBS_FILE = APPLY_ROOT / "jobs.xlsx"
APPLY_OUTPUT_FILE = APPLY_ROOT / "jobs_output.xlsx"
DETAILS_FILE = APPLY_ROOT / "details.xlsx"

FIELDNAMES = [
    "Title", "Company", "Platform", "URL",
    "Status", "Remark", "Applied Status", "Applied Date", "Company Site URL",
    "External Portal", "External ATS", "External Domain", "Redirect URL",
]


def get_state():
    return STATE


def _ts():
    return datetime.now().strftime("%H:%M:%S")


def _log(msg: str):
    text = str(msg or "").strip()
    STATE["log"].append({"ts": _ts(), "msg": text})
    print(f"[{_ts()}] {text}", flush=True)


def load_jobs_file():
    if DATA_JOBS_FILE.exists():
        try:
            return json.loads(DATA_JOBS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_jobs_file(jobs: dict):
    DATA_JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_JOBS_FILE.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_ref(ref):
    return str(ref or "").strip().lower()


def _select_jobs(job_refs: list | None, filters: dict | None = None):
    jobs = load_jobs_file()

    if job_refs:
        normalized = {_normalize_ref(r) for r in job_refs if r is not None}
        selected = []
        for job in jobs.values():
            if _normalize_ref(job.get("Job ID")) in normalized:
                selected.append(job)
                continue
            if _normalize_ref(job.get("URL")) in normalized:
                selected.append(job)
                continue
            if _normalize_ref(job.get("id")) in normalized:
                selected.append(job)
                continue
        return selected

    filters = filters or {}
    statuses = {s.strip().lower() for s in (filters.get("statuses") or ["New"]) if s}
    platforms = {p.strip().lower() for p in (filters.get("platforms") or []) if p}
    min_score = filters.get("min_score")
    date_from = filters.get("date_from")
    date_to = filters.get("date_to")
    location = (filters.get("location") or "").strip().lower()
    search = (filters.get("search") or "").strip().lower()

    selected = []
    for job in jobs.values():
        status = str(job.get("Status", "New")).strip().lower()
        if statuses and status not in statuses:
            continue
        if platforms and str(job.get("Platform", "")).strip().lower() not in platforms:
            continue
        if min_score not in (None, ""):
            try:
                if float(job.get("AI Score") or 0) < float(min_score):
                    continue
            except (TypeError, ValueError):
                pass
        scraped = str(job.get("Scraped At", ""))[:10]
        if date_from and scraped and scraped < date_from:
            continue
        if date_to and scraped and scraped > date_to:
            continue
        if location and location not in str(job.get("Location", "")).lower():
            continue
        if search:
            haystack = " ".join(str(job.get(k, "")) for k in ("Title", "Company", "Key Skills")).lower()
            if search not in haystack:
                continue
        selected.append(job)
    return selected


def _build_apply_workbook(jobs: list[dict]):
    wb = Workbook()
    ws = wb.active
    ws.title = "Jobs"
    ws.append(FIELDNAMES)
    for job in jobs:
        ws.append([
            job.get("Title", ""),
            job.get("Company", ""),
            job.get("Platform", ""),
            job.get("URL", ""),
            "New",
            "",
            "",
            "",
            "",
            "",   # External Portal
            "",   # External ATS
            "",   # External Domain
            "",   # Redirect URL
        ])
    wb.save(APPLY_JOBS_FILE)
    _log(f"💾 Built apply spreadsheet with {len(jobs)} jobs")


def _sync_apply_results():
    if not APPLY_OUTPUT_FILE.exists():
        _log("⚠ No apply output file found. Skipping sync.")
        STATE["summary"] = {"updated_rows": 0, "applied_jobs": 0, "external_portal_jobs": 0, "total_rows": 0, "status_breakdown": {}}
        return

    try:
        wb = load_workbook(APPLY_OUTPUT_FILE)
        ws = wb.active
        headers = [str(c.value).strip() for c in ws[1] if c.value]
        rows = [
            dict(zip(headers, [str(v).strip() if v is not None else "" for v in row]))
            for row in ws.iter_rows(min_row=2, values_only=True)
        ]
    except Exception as exc:
        _log(f"❌ Failed to read apply output workbook: {exc}")
        STATE["summary"] = {"updated_rows": 0, "applied_jobs": 0, "external_portal_jobs": 0, "total_rows": 0, "status_breakdown": {}}
        return

    jobs_dict = load_jobs_file()
    updated = 0
    applied_count = 0
    external_count = 0
    synced_urls = set()
    status_breakdown: dict = {}
    run_results: list = []

    db = SessionLocal()
    try:
        for row in rows:
            url = str(row.get("URL", "")).strip()
            if not url:
                continue

            job_key = next(
                (k for k, v in jobs_dict.items() if str(v.get("URL", "")).strip().lower() == url.lower()),
                None,
            )
            if not job_key:
                job_key = next(
                    (k for k, v in jobs_dict.items() if str(v.get("Job ID", "")).strip().lower() == _normalize_ref(row.get("Job ID"))),
                    None,
                )

            job = jobs_dict.get(job_key) if job_key else None
            if job:
                changed = False
                status_value = row.get("Status") or row.get("Applied Status")
                if status_value and job.get("Status") != status_value:
                    job["Status"] = status_value
                    changed = True
                if row.get("Applied Date") and job.get("Applied Date") != row.get("Applied Date"):
                    job["Applied Date"] = row.get("Applied Date")
                    changed = True
                if row.get("Company Site URL") and job.get("Company Site URL") != row.get("Company Site URL"):
                    job["Company Site URL"] = row.get("Company Site URL")
                    changed = True
                if row.get("External Portal") and job.get("External Portal") != row.get("External Portal"):
                    job["External Portal"] = row.get("External Portal")
                    job["External ATS"] = row.get("External ATS", "")
                    job["External Domain"] = row.get("External Domain", "")
                    job["Redirect URL"] = row.get("Redirect URL", "")
                    changed = True
                if row.get("Remark"):
                    notes = str(job.get("Notes", "")).strip()
                    remark = str(row.get("Remark", "")).strip()
                    if remark and remark not in notes:
                        job["Notes"] = f"{notes}\n{remark}".strip()
                        changed = True
                if changed:
                    jobs_dict[job_key] = job
                    updated += 1

            try:
                db_job = db.query(Job).filter(Job.url == url).first()
                if not db_job and job:
                    db_job = upsert_job(db, {
                        "job_id": job.get("Job ID", ""),
                        "title": job.get("Title", ""),
                        "company": job.get("Company", ""),
                        "location": job.get("Location", ""),
                        "salary": job.get("Salary", ""),
                        "experience": job.get("Experience", ""),
                        "source": job.get("Platform", ""),
                        "url": job.get("URL", ""),
                        "description": job.get("Job Description", ""),
                        "key_skills": job.get("Key Skills", ""),
                        "job_highlights": job.get("Job Highlights", ""),
                        "role": job.get("Role", ""),
                        "industry_type": job.get("Industry Type", ""),
                        "department": job.get("Department", ""),
                        "education": job.get("Education", ""),
                        "listing": job.get("Listing", ""),
                        "keyword": job.get("Keyword", ""),
                        "match_score": float(job.get("Match Score") or 0),
                        "ai_score": float(job.get("AI Score") or 0),
                        "ai_reason": job.get("AI Reason", ""),
                        "status": job.get("Status", "New"),
                        "scraped_at": job.get("Scraped At", ""),
                    })

                if db_job:
                    updates = {}
                    status_value = row.get("Status") or row.get("Applied Status")
                    if status_value and db_job.status != status_value:
                        updates["status"] = status_value
                    if row.get("Applied Date") and db_job.applied_date != row.get("Applied Date"):
                        updates["applied_date"] = row.get("Applied Date")
                    if row.get("External Portal") and db_job.external_portal != row.get("External Portal"):
                        updates["external_portal"] = row.get("External Portal")
                        updates["external_ats"] = row.get("External ATS", "")
                        updates["external_domain"] = row.get("External Domain", "")
                        updates["redirect_url"] = row.get("Redirect URL", "")
                    if row.get("Remark"):
                        existing_notes = str(db_job.notes or "").strip()
                        remark = str(row.get("Remark", "")).strip()
                        if remark and remark not in existing_notes:
                            updates["notes"] = f"{existing_notes}\n{remark}".strip()
                    if row.get("Company Site URL"):
                        existing_notes = updates.get("notes", str(db_job.notes or "")).strip()
                        url_note = str(row.get("Company Site URL", "")).strip()
                        if url_note and url_note not in existing_notes:
                            updates["notes"] = f"{existing_notes}\nCompany Site URL: {url_note}".strip()
                    if updates:
                        update_job(db, db_job.id, updates)
            except Exception as exc:
                db.rollback()
                _log(f"⚠ DB sync skipped for {url}: {exc}")

            if status_value and str(status_value).strip().lower() in {"applied", "already applied", "company site"}:
                applied_count += 1
            if str(row.get("External Portal", "")).strip().lower() == "true":
                external_count += 1
            synced_urls.add(url)

            status_key = str(status_value or "New").strip() or "New"
            status_breakdown[status_key] = status_breakdown.get(status_key, 0) + 1
            run_results.append({
                "Title": row.get("Title", ""),
                "Company": row.get("Company", ""),
                "Platform": row.get("Platform", ""),
                "URL": url,
                "Status": status_key,
                "Remark": row.get("Remark", ""),
                "External ATS": row.get("External ATS", ""),
                "Redirect URL": row.get("Redirect URL") or row.get("Company Site URL", ""),
            })

    except Exception as exc:
        _log(f"❌ DB sync failed: {exc}")
    finally:
        db.close()

    save_jobs_file(jobs_dict)
    STATE["summary"] = {
        "updated_rows": updated,
        "applied_jobs": applied_count,
        "external_portal_jobs": external_count,
        "total_rows": len(rows),
        "synced_urls": len(synced_urls),
        "status_breakdown": status_breakdown,
    }
    STATE["results"] = run_results
    _log(f"🔁 Synced {updated} job(s) to JSON/DB from apply output")


def _run_apply_job(job_refs: list | None = None, filters: dict | None = None):
    if STATE["running"]:
        return

    STATE["running"] = True
    STATE["log"] = []
    STATE["summary"] = {}
    STATE["results"] = []

    try:
        if not DETAILS_FILE.exists():
            _log("❌ Auto-apply profile missing: Apply/Apply-2/details.xlsx")
            return

        selected_jobs = _select_jobs(job_refs, filters)
        if not selected_jobs:
            _log("⚠ No jobs matched your selection. Adjust the filters (platforms/status/date/score) or add jobs with Status=New.")
            return

        _build_apply_workbook(selected_jobs)

        if APPLY_OUTPUT_FILE.exists():
            try:
                APPLY_OUTPUT_FILE.unlink()
            except Exception:
                pass

        from app.automation.browser import is_chrome_available
        if not is_chrome_available():
            _log("⚠ Automated browser auto-apply requires Google Chrome.")
            _log("ℹ Note: Current server environment does not have Google Chrome installed.")
            _log("💡 Options for applying:")
            _log("   1. Open any job in the Jobs tab and tap 'Apply on Portal ↗' with your tailored AI pack.")
            _log("   2. Run locally on your PC (python run.py) where your Chrome browser is logged in.")
            _log("   3. Deploy via Docker with Chrome pre-installed.")
            STATE["summary"] = {
                "updated_rows": 0,
                "applied_jobs": 0,
                "external_portal_jobs": 0,
                "total_rows": len(selected_jobs),
                "synced_urls": 0,
                "status_breakdown": {"Chrome Required": len(selected_jobs)},
            }
            return

        command = [sys.executable, str(APPLY_ROOT / "app.py")]
        _log(f"🚀 Starting auto-apply for {len(selected_jobs)} jobs")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        process = subprocess.Popen(
            command,
            cwd=str(APPLY_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )

        if process.stdout:
            for line in process.stdout:
                _log(line.rstrip())

        process.wait()
        _log(f"🏁 Auto-apply process completed with exit code {process.returncode}")

        _sync_apply_results()

        if process.returncode != 0:
            _log("⚠ Auto-apply completed with errors. Review the log above.")
    except Exception as exc:
        _log(f"❌ Auto-apply failed: {exc}")
    finally:
        STATE["running"] = False
        STATE["last_run"] = datetime.now().isoformat()


def start_apply_thread(job_refs: list | None = None, filters: dict | None = None):
    if STATE["running"]:
        return False, "Auto-apply already in progress"

    thread = threading.Thread(target=_run_apply_job, args=(job_refs, filters), daemon=True)
    thread.start()
    return True, "Apply started"
