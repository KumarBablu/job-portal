"""
Bablu Kumar — Personal AI Job Agent
Flask backend with all API routes.
Run: python run.py
Then open: http://127.0.0.1:5000
"""
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, jsonify, request, send_file

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True, encoding="utf-8")

# ── Database bootstrap ────────────────────────────────────────────────────────
from app.database.db import Base, engine, SessionLocal
from app.models.job_model import Job  # noqa: ensures model is registered
from sqlalchemy import text

Base.metadata.create_all(bind=engine)

# Ensure older DB files with missing job detail columns are automatically migrated
def ensure_db_schema():
    desired_columns = {
        "key_skills": "TEXT DEFAULT ''",
        "job_highlights": "TEXT DEFAULT ''",
        "role": "TEXT DEFAULT ''",
        "industry_type": "TEXT DEFAULT ''",
        "department": "TEXT DEFAULT ''",
        "employment_type": "TEXT DEFAULT ''",
        "education": "TEXT DEFAULT ''",
        "unique_id": "TEXT DEFAULT ''",
        # Outreach Agent columns
        "outreach_status": "TEXT DEFAULT 'Not Started'",
        "outreach_channel": "TEXT DEFAULT 'LinkedIn'",
        "recruiter_name": "TEXT DEFAULT ''",
        "recruiter_contact": "TEXT DEFAULT ''",
        "outreach_message": "TEXT DEFAULT ''",
        "outreach_sent_date": "TEXT DEFAULT ''",
        "outreach_followup_date": "TEXT DEFAULT ''",
        "outreach_notes": "TEXT DEFAULT ''",
        # External-portal tracking (redirect-and-skip auto-apply flow)
        "external_portal": "TEXT DEFAULT ''",
        "external_ats": "TEXT DEFAULT ''",
        "external_domain": "TEXT DEFAULT ''",
        "redirect_url": "TEXT DEFAULT ''",
    }

    with engine.begin() as conn:
        existing = [row[1] for row in conn.execute(text("PRAGMA table_info(jobs);"))]
        for column_name, column_def in desired_columns.items():
            if column_name not in existing:
                conn.execute(text(f"ALTER TABLE jobs ADD COLUMN {column_name} {column_def};"))

ensure_db_schema()

# ── Services ──────────────────────────────────────────────────────────────────
from sqlalchemy.exc import OperationalError
from app.services.job_service import (
    get_all_jobs, get_job_by_id, get_stats, update_job, upsert_job, get_outreach_queue,
    delete_job, delete_jobs_by_ids, delete_jobs_by_filter
)

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR  = Path(__file__).resolve().parent
JOBS_FILE = BASE_DIR / "data" / "jobs" / "jobs_data.json"

# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(
    __name__,
    template_folder="frontend/templates",
    static_folder="frontend/static",
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    force=True,
)
app.logger.setLevel(logging.INFO)
app.logger.handlers.clear()
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
app.logger.addHandler(stream_handler)
app.logger.propagate = False

# ──────────────────────────────────────────────────────────────────────────────
# CANDIDATE (from Resume: NoBroker Technologies & Udaan)
# ──────────────────────────────────────────────────────────────────────────────
CANDIDATE = {
    "name":       "Bablu Kumar",
    "experience": "3.5 Years Experience",
    "location":   "Bengaluru, Karnataka",
    "email":      "bablusoni2825@gmail.com",
    "phone":      "+91 9771039201",
    "skills":     ["SQL", "Python", "Power BI", "DAX", "Advanced Excel",
                   "ETL", "KPI Reporting", "Dashboard Development",
                   "Business Intelligence", "Cohort & Retention Analysis",
                   "Pandas", "NumPy", "Snowflake", "Tableau", "AWS S3", "GCP"],
}


# ──────────────────────────────────────────────────────────────────────────────
# IN-MEMORY CACHE (Eliminates disk re-reads and SQLite latency on API requests)
# ──────────────────────────────────────────────────────────────────────────────
_JOBS_CACHE = {
    "mtime": 0,
    "jobs_json": {},
    "merged_jobs": [],
    "stats": None
}

def invalidate_jobs_cache():
    _JOBS_CACHE["mtime"] = 0
    _JOBS_CACHE["jobs_json"] = {}
    _JOBS_CACHE["merged_jobs"] = []
    _JOBS_CACHE["stats"] = None


def get_cached_jobs_json() -> dict:
    if not JOBS_FILE.exists():
        return {}
    try:
        current_mtime = JOBS_FILE.stat().st_mtime
        if _JOBS_CACHE["mtime"] != current_mtime or not _JOBS_CACHE["jobs_json"]:
            _JOBS_CACHE["jobs_json"] = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
            _JOBS_CACHE["mtime"] = current_mtime
            _JOBS_CACHE["merged_jobs"] = []
            _JOBS_CACHE["stats"] = None
    except Exception:
        pass
    return _JOBS_CACHE["jobs_json"]


def get_cached_merged_jobs() -> list:
    if _JOBS_CACHE["merged_jobs"]:
        return _JOBS_CACHE["merged_jobs"]

    jobs_json = get_cached_jobs_json()
    if not jobs_json:
        try:
            db = SessionLocal()
            try:
                jobs = get_all_jobs(db)
                _JOBS_CACHE["merged_jobs"] = [job_to_dict(j) for j in jobs]
                return _JOBS_CACHE["merged_jobs"]
            finally:
                db.close()
        except Exception:
            return []

    try:
        db = SessionLocal()
        try:
            db_jobs = {j.url: j for j in get_all_jobs(db)}
            result = []
            for jid, job in jobs_json.items():
                url = job.get("URL", "")
                db_job = db_jobs.get(url)
                if db_job:
                    detail_fields = {
                        "Job Description": db_job.description,
                        "Key Skills": db_job.key_skills,
                        "Job Highlights": db_job.job_highlights,
                        "Role": db_job.role,
                        "Industry Type": db_job.industry_type,
                        "Department": db_job.department,
                        "Employment Type": db_job.employment_type,
                        "Education": db_job.education,
                    }
                    for field, value in detail_fields.items():
                        if str(value or "").strip():
                            job[field] = value
                    job["Status"]         = db_job.status or job.get("Status", "New")
                    job["Applied Date"]   = db_job.applied_date or job.get("Applied Date", "")
                    job["Contact"]        = db_job.contact or job.get("Contact", "")
                    job["Interview Date"] = db_job.interview_date or job.get("Interview Date", "")
                    job["Follow-Up"]      = db_job.follow_up or job.get("Follow-Up", "")
                    job["Result"]         = db_job.result or job.get("Result", "")
                    job["Cover Letter"]   = db_job.cover_letter or job.get("Cover Letter", "")
                    job["Notes"]          = db_job.notes or job.get("Notes", "")
                    if db_job.external_portal:
                        job["External Portal"] = db_job.external_portal
                        job["External ATS"]    = db_job.external_ats or job.get("External ATS", "")
                        job["External Domain"] = db_job.external_domain or job.get("External Domain", "")
                        job["Redirect URL"]    = db_job.redirect_url or job.get("Redirect URL", "")
                    job["id"]             = db_job.id
                result.append(job)
        finally:
            db.close()

        result.sort(key=lambda x: float(x.get("AI Score") or 0), reverse=True)
        _JOBS_CACHE["merged_jobs"] = result
        return result
    except Exception as e:
        result = list(jobs_json.values())
        result.sort(key=lambda x: float(x.get("AI Score") or 0), reverse=True)
        _JOBS_CACHE["merged_jobs"] = result
        return result


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def load_jobs_json() -> dict:
    return get_cached_jobs_json()


def save_jobs_json(jobs: dict):
    JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    JOBS_FILE.write_text(
        json.dumps(jobs, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    invalidate_jobs_cache()


def resolve_job_payload(job_data: dict, job_ref: str = None) -> dict:
    if job_data:
        return job_data

    if not job_ref:
        return {}

    try:
        db = SessionLocal()
        try:
            job = None
            try:
                job = get_job_by_id(db, int(job_ref))
            except (ValueError, TypeError):
                pass

            if not job:
                job = db.query(Job).filter(Job.job_id == job_ref).first()

            if job:
                return job_to_dict(job)
        finally:
            db.close()
    except Exception:
        pass

    jobs_json = load_jobs_json()
    return jobs_json.get(job_ref, {})


def job_to_dict(j: Job) -> dict:
    return {
        "id":            j.id,
        "Job ID":        j.job_id or str(j.id),
        "Unique ID":     j.unique_id or "",
        "Title":         j.title or "",
        "Company":       j.company or "",
        "Location":      j.location or "",
        "Salary":        j.salary or "",
        "Experience":    j.experience or "",
        "Platform":      j.source or "",
        "URL":           j.url or "",
        "Keyword":       j.keyword or "",
        "Listing":       j.listing or "",
        "Job Description": j.description or "",
        "Key Skills":      j.key_skills or "",
        "Job Highlights":  j.job_highlights or "",
        "Role":            j.role or "",
        "Industry Type":   j.industry_type or "",
        "Department":      j.department or "",
        "Employment Type": j.employment_type or "",
        "Education":       j.education or "",
        "AI Score":      round(j.ai_score or 0, 1),
        "Match Score":   round(j.match_score or 0, 1),
        "AI Reason":     j.ai_reason or "",
        "Status":        j.status or "New",
        "Applied Date":  j.applied_date or "",
        "Contact":       j.contact or "",
        "Interview Date":j.interview_date or "",
        "Follow-Up":     j.follow_up or "",
        "Result":        j.result or "",
        "Cover Letter":  j.cover_letter or "",
        "Notes":         j.notes or "",
        "Scraped At":    j.scraped_at or "",
        # External-portal tracking
        "External Portal": j.external_portal or "",
        "External ATS":     j.external_ats or "",
        "External Domain":  j.external_domain or "",
        "Redirect URL":     j.redirect_url or "",
        # Outreach Agent
        "Outreach Status":         j.outreach_status or "Not Started",
        "Outreach Channel":        j.outreach_channel or "LinkedIn",
        "Recruiter Name":          j.recruiter_name or "",
        "Recruiter Contact":       j.recruiter_contact or "",
        "Outreach Message":        j.outreach_message or "",
        "Outreach Sent Date":      j.outreach_sent_date or "",
        "Outreach Follow-Up Date": j.outreach_followup_date or "",
        "Outreach Notes":          j.outreach_notes or "",
    }


# ──────────────────────────────────────────────────────────────────────────────
# PAGES
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return render_template("index.html", candidate=CANDIDATE)


# ──────────────────────────────────────────────────────────────────────────────
# API — JOBS
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/jobs")
def api_jobs():
    """Return all jobs, served from in-memory cache with DB overlay."""
    return jsonify(get_cached_merged_jobs())


@app.route("/api/job/<job_ref>", methods=["GET", "POST"])
def api_job(job_ref):
    """GET or POST a specific job by ID or Job ID string."""
    try:
        db = SessionLocal()
        try:
            # Try numeric DB id first, then job_id string
            job = None
            try:
                job = get_job_by_id(db, int(job_ref))
            except (ValueError, TypeError):
                pass

            if not job:
                job = db.query(Job).filter(Job.job_id == job_ref).first()

            if request.method == "GET":
                if not job:
                    # Try from JSON file
                    jobs_json = load_jobs_json()
                    if job_ref in jobs_json:
                        return jsonify(jobs_json[job_ref])
                    return jsonify({"error": "Not found"}), 404
                return jsonify(job_to_dict(job))

            # POST — update tracking fields
            data = request.get_json(force=True) or {}

            # If job doesn't exist in DB yet, create it from JSON
            if not job:
                jobs_json = load_jobs_json()
                job_data  = jobs_json.get(job_ref, {})
                if not job_data:
                    return jsonify({"ok": False, "msg": "Job not found"}), 404
                job = upsert_job(db, {
                    "job_id":  job_ref,
                    "title":   job_data.get("Title", ""),
                    "company": job_data.get("Company", ""),
                    "location":job_data.get("Location", ""),
                    "url":     job_data.get("URL", ""),
                    "source":  job_data.get("Platform", ""),
                    "ai_score":float(job_data.get("AI Score") or 0),
                    "match_score": float(job_data.get("Match Score") or 0),
                    "scraped_at": job_data.get("Scraped At", ""),
                    "status":  job_data.get("Status", "New"),
                    "description": job_data.get("Job Description", "") or job_data.get("description", ""),
                    "key_skills": job_data.get("Key Skills", "") or job_data.get("key_skills", ""),
                    "job_highlights": job_data.get("Job Highlights", "") or job_data.get("job_highlights", ""),
                    "role": job_data.get("Role", "") or job_data.get("role", ""),
                    "industry_type": job_data.get("Industry Type", "") or job_data.get("industry_type", ""),
                    "department": job_data.get("Department", "") or job_data.get("department", ""),
                    "education": job_data.get("Education", "") or job_data.get("education", ""),
                    "unique_id": job_data.get("Unique ID", "") or job_data.get("unique_id", ""),
                })

            if not job:
                return jsonify({"ok": False, "msg": "Could not create job"}), 500

            # Map incoming fields to model attributes
            field_map = {
                "Status":          "status",
                "Applied Date":    "applied_date",
                "Contact":         "contact",
                "Interview Date":  "interview_date",
                "Follow-Up":       "follow_up",
                "Result":          "result",
                "Cover Letter":    "cover_letter",
                "Notes":           "notes",
                # Outreach Agent fields
                "Outreach Status":         "outreach_status",
                "Outreach Channel":        "outreach_channel",
                "Recruiter Name":          "recruiter_name",
                "Recruiter Contact":       "recruiter_contact",
                "Outreach Message":        "outreach_message",
                "Outreach Sent Date":      "outreach_sent_date",
                "Outreach Follow-Up Date": "outreach_followup_date",
                "Outreach Notes":          "outreach_notes",
                # snake_case versions too
                "status":          "status",
                "applied_date":    "applied_date",
                "contact":         "contact",
                "interview_date":  "interview_date",
                "follow_up":       "follow_up",
                "result":          "result",
                "cover_letter":    "cover_letter",
                "notes":           "notes",
                "outreach_status":         "outreach_status",
                "outreach_channel":        "outreach_channel",
                "recruiter_name":          "recruiter_name",
                "recruiter_contact":       "recruiter_contact",
                "outreach_message":        "outreach_message",
                "outreach_sent_date":      "outreach_sent_date",
                "outreach_followup_date":  "outreach_followup_date",
                "outreach_notes":          "outreach_notes",
            }

            db_update = {}
            for k, v in data.items():
                if k in field_map:
                    db_update[field_map[k]] = v

            updated = update_job(db, job.id, db_update)

            # Also update JSON file
            jobs_json = load_jobs_json()
            if job_ref in jobs_json:
                for k, v in data.items():
                    jobs_json[job_ref][k] = v
                save_jobs_json(jobs_json)

            return jsonify({"ok": True, "job": job_to_dict(updated)})
        finally:
            db.close()
    except OperationalError as e:
        print(f"DB OperationalError: {e}")
        # Fall back to JSON file if DB schema is incompatible
        jobs_json = load_jobs_json()
        if request.method == "GET":
            if job_ref in jobs_json:
                return jsonify(jobs_json[job_ref])
            return jsonify({"error": "Not found (DB schema mismatch)"}), 404
        # For POST updates, return error indicating DB needs migration
        return jsonify({"ok": False, "msg": "Database schema incompatible. Please recreate the DB."}), 500


# ──────────────────────────────────────────────────────────────────────────────
# API — SEARCH JOBS BY ID
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/search/job-by-id/<query>")
def api_search_job_by_id(query):
    """Search for a job by ID (numeric or job_id string)."""
    db = SessionLocal()
    try:
        # Try numeric ID first
        try:
            job = get_job_by_id(db, int(query))
            if job:
                return jsonify(job_to_dict(job))
        except (ValueError, TypeError):
            pass

        # Try job_id string match
        job = db.query(Job).filter(Job.job_id == query).first()
        if job:
            return jsonify(job_to_dict(job))

        # Try partial job_id match (contains)
        jobs = db.query(Job).filter(Job.job_id.like(f"%{query}%")).all()
        if jobs:
            return jsonify([job_to_dict(j) for j in jobs])

        # Try from JSON file
        jobs_json = load_jobs_json()
        if query in jobs_json:
            return jsonify(jobs_json[query])

        # Partial search in JSON keys
        matches = {k: v for k, v in jobs_json.items() if query.lower() in k.lower()}
        if matches:
            return jsonify(list(matches.values()))

        return jsonify({"error": f"No job found with ID: {query}"}), 404

    finally:
        db.close()


# ──────────────────────────────────────────────────────────────────────────────
# API — DELETE JOBS
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/jobs/delete", methods=["POST"])
def api_delete_jobs():
    """Delete jobs by IDs/URLs or filters.
    POST data can include:
    - job_ids: list of job IDs/URLs to delete
    - filters: dict with filter criteria (status, platform, ai_score_min, etc.)
    Returns count of deleted jobs."""
    data = request.get_json(force=True) or {}
    job_ids = data.get("job_ids", [])
    filters = data.get("filters", {})
    
    if not job_ids and not filters:
        return jsonify({"ok": False, "msg": "Either job_ids or filters must be provided"}), 400
    
    db = SessionLocal()
    try:
        deleted_count = 0
        
        # Delete by specific IDs if provided
        if job_ids:
            deleted_count += delete_jobs_by_ids(db, job_ids)
        
        # Delete by filters if provided
        if filters:
            deleted_count += delete_jobs_by_filter(db, filters)
        
        # Also remove from JSON file if present
        if job_ids:
            jobs_json = load_jobs_json()
            normalized_ids = {str(id).strip().lower() for id in job_ids}
            updated_json = {}
            for job_id, job_data in jobs_json.items():
                url = job_data.get("URL", "")
                if not (str(job_id or "").strip().lower() in normalized_ids or 
                        str(url or "").strip().lower() in normalized_ids):
                    updated_json[job_id] = job_data
            if len(updated_json) < len(jobs_json):
                save_jobs_json(updated_json)
        
        return jsonify({"ok": True, "deleted": deleted_count, "msg": f"Deleted {deleted_count} job(s)"})
    except Exception as e:
        app.logger.error(f"Error deleting jobs: {e}")
        return jsonify({"ok": False, "msg": f"Error deleting jobs: {str(e)}"}), 500
    finally:
        db.close()


@app.route("/api/job/<job_ref>/delete", methods=["POST"])
def api_delete_job(job_ref):
    """Delete a single job by ID or URL."""
    db = SessionLocal()
    try:
        # Try numeric ID first
        deleted = False
        try:
            job_id = int(job_ref)
            deleted = delete_job(db, job_id)
        except (ValueError, TypeError):
            pass
        
        # Try by job_id string or URL
        if not deleted:
            deleted = (delete_jobs_by_ids(db, [job_ref]) > 0)
        
        if not deleted:
            return jsonify({"ok": False, "msg": "Job not found"}), 404
        
        # Also remove from JSON file if present
        jobs_json = load_jobs_json()
        updated_json = {}
        for job_id, job_data in jobs_json.items():
            url = job_data.get("URL", "")
            if not (str(job_id or "").strip().lower() == str(job_ref or "").strip().lower() or 
                    str(url or "").strip().lower() == str(job_ref or "").strip().lower()):
                updated_json[job_id] = job_data
        if len(updated_json) < len(jobs_json):
            save_jobs_json(updated_json)
        
        return jsonify({"ok": True, "msg": "Job deleted successfully"})
    except Exception as e:
        app.logger.error(f"Error deleting job: {e}")
        return jsonify({"ok": False, "msg": f"Error deleting job: {str(e)}"}), 500
    finally:
        db.close()


# ──────────────────────────────────────────────────────────────────────────────
# API — STATS
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/stats")
def api_stats():
    """Return pipeline & platform statistics, served from cache."""
    if _JOBS_CACHE["stats"] is not None:
        return jsonify(_JOBS_CACHE["stats"])

    jobs = get_cached_merged_jobs()
    by_platform = {}
    for j in jobs:
        p = j.get("Platform", "Unknown")
        by_platform[p] = by_platform.get(p, 0) + 1

    stats = {
        "total":       len(jobs),
        "top":         len([j for j in jobs if float(j.get("AI Score") or 0) >= 60]),
        "applied":     len([j for j in jobs if j.get("Status") == "Applied"]),
        "already_applied": len([j for j in jobs if j.get("Status") == "Already Applied"]),
        "interviews":  len([j for j in jobs if j.get("Status") == "Interviewing"]),
        "offers":      len([j for j in jobs if j.get("Status") == "Offer"]),
        "rejected":    len([j for j in jobs if j.get("Status") == "Rejected"]),
        "external_portal": len([j for j in jobs if j.get("Status") == "External Portal"]),
        "errors":      len([j for j in jobs if j.get("Status") == "Error"]),
        "new":         len([j for j in jobs if (j.get("Status") or "New") == "New"]),
        "by_platform": by_platform,
    }
    _JOBS_CACHE["stats"] = stats
    return jsonify(stats)


# ──────────────────────────────────────────────────────────────────────────────
# API — SCRAPE
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    from app.scrapers.orchestrator import start_scrape_thread, get_state
    state = get_state()
    if state["running"]:
        return jsonify({"ok": False, "msg": "Scraper already running"}), 409

    config = request.get_json(force=True) or {}
    ok, msg = start_scrape_thread(config)
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/scrape/log")
def api_scrape_log():
    from app.scrapers.orchestrator import get_state
    state = get_state()
    return jsonify({"log": state["log"], "running": state["running"]})


@app.route("/api/apply", methods=["POST"])
def api_apply():
    data = request.get_json(force=True) or {}
    job_refs = data.get("job_refs")
    filters = data.get("filters")
    from app.apply_runner import start_apply_thread
    ok, msg = start_apply_thread(job_refs, filters)
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/apply/status")
def api_apply_status():
    from app.apply_runner import get_state as get_apply_state
    state = get_apply_state()
    return jsonify({
        "running": state["running"],
        "last_run": state.get("last_run"),
        "summary": state.get("summary", {}),
        "results": state.get("results", []),
    })


@app.route("/api/apply/results")
def api_apply_results():
    """Every job's outcome + remark from the most recent auto-apply run —
    the basis for the 'view/download all remarks' panel, so the user
    doesn't have to download separate files per status."""
    from app.apply_runner import get_state as get_apply_state
    state = get_apply_state()
    return jsonify({
        "ok": True,
        "running": state["running"],
        "summary": state.get("summary", {}),
        "results": state.get("results", []),
    })


@app.route("/api/apply/results/download")
def api_apply_results_download():
    """Download every job's outcome + remark from the most recent auto-apply
    run as a single Excel file — one file covering every status, instead of
    downloading a separate file per status."""
    from app.apply_runner import get_state as get_apply_state
    state = get_apply_state()
    results = state.get("results", [])
    if not results:
        return jsonify({"error": "No auto-apply results yet — run auto-apply first."}), 404

    try:
        import pandas as pd
    except Exception:
        return jsonify({"error": "pandas/openpyxl not installed"}), 500

    df = pd.DataFrame(results, columns=[
        "Title", "Company", "Platform", "Status", "Remark",
        "External ATS", "Redirect URL", "URL",
    ])
    export_dir = Path(__file__).resolve().parent / "data" / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    path = export_dir / "Auto_Apply_Results.xlsx"
    df.to_excel(path, index=False)

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    return send_file(str(path), as_attachment=True,
                     download_name=f"Auto_Apply_Results_{stamp}.xlsx")


@app.route("/api/apply/log")
def api_apply_log():
    from app.apply_runner import get_state as get_apply_state
    state = get_apply_state()
    return jsonify({"log": state["log"], "running": state["running"]})


@app.route("/api/apply/external-portals")
def api_apply_external_portals():
    """Jobs the auto-apply bot intentionally skipped because they redirected
    off-platform to an external ATS/company site — these need a manual
    follow-up. See Apply/Apply-2/app.py's redirect-and-skip flow."""
    jobs_json = load_jobs_json()
    if jobs_json:
        matches = [
            j for j in jobs_json.values()
            if str(j.get("External Portal", "")).strip().lower() == "true"
        ]
        matches.sort(key=lambda j: float(j.get("AI Score") or 0), reverse=True)
        return jsonify({"ok": True, "jobs": matches})

    db = SessionLocal()
    try:
        from app.services.job_service import get_external_portal_jobs
        jobs = get_external_portal_jobs(db)
        return jsonify({"ok": True, "jobs": [job_to_dict(j) for j in jobs]})
    finally:
        db.close()


# ──────────────────────────────────────────────────────────────────────────────
# API — AI TOOLS
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/cover_letter", methods=["POST"])
def api_cover_letter():
    data    = request.get_json(force=True) or {}
    title   = data.get("title", "Data Analyst")
    company = data.get("company", "the company")
    location= data.get("location", "Bangalore")
    notes   = data.get("notes", "")

    from app.ai.scorer import generate_cover_letter
    result = generate_cover_letter(title, company, location, notes)
    return jsonify({"result": result})


@app.route("/api/interview_prep", methods=["POST"])
def api_interview_prep():
    data    = request.get_json(force=True) or {}
    title   = data.get("title", "Data Analyst")
    company = data.get("company", "")

    from app.ai.scorer import generate_interview_prep
    result = generate_interview_prep(title, company)
    return jsonify({"result": result})


@app.route("/api/gap_analysis", methods=["POST"])
def api_gap_analysis():
    jobs_json = load_jobs_json()
    titles   = [j.get("Title", "") for j in jobs_json.values()]
    listings = [j.get("Listing", "") for j in jobs_json.values() if j.get("Listing")]

    from app.ai.scorer import generate_gap_analysis
    result = generate_gap_analysis(titles, listings)
    return jsonify({"result": result})


# ──────────────────────────────────────────────────────────────────────────────
# API — SETTINGS
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/settings", methods=["POST"])
def api_settings():
    """Settings endpoint. Currently no API key needed for free AI."""
    data = request.get_json(force=True) or {}
    # No API key needed - all AI text generation runs locally via templates
    return jsonify({"ok": True, "message": "No API key required - using free AI"})


# ──────────────────────────────────────────────────────────────────────────────
# API — DOWNLOAD EXCEL
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/download_excel")
def api_download_excel():
    """Export jobs to Excel. Any of the query params below narrow the export
    to exactly what's currently filtered in the Jobs table:
    status, platform, outreach_status, min_score, max_score, search, location,
    date_from, date_to (against Scraped At), ids (comma-separated Job IDs/URLs)."""
    try:
        filters = {
            "status":          request.args.get("status") or None,
            "platform":        request.args.get("platform") or None,
            "outreach_status": request.args.get("outreach_status") or None,
            "min_score":       request.args.get("min_score") or None,
            "max_score":       request.args.get("max_score") or None,
            "search":          request.args.get("search") or None,
            "location":        request.args.get("location") or None,
            "date_from":       request.args.get("date_from") or None,
            "date_to":         request.args.get("date_to") or None,
        }
        ids_param = request.args.get("ids")
        if ids_param:
            filters["ids"] = [i for i in ids_param.split(",") if i.strip()]

        filters = {k: v for k, v in filters.items() if v not in (None, "")}

        from app.utils.excel_export import export_to_excel
        try:
            path = export_to_excel(filters or None)
        except ValueError as e:
            # "No jobs to export" / "No jobs match the selected filters" —
            # an empty result set, not a server error.
            return jsonify({"error": str(e)}), 404

        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        suffix = "_filtered" if filters else ""
        return send_file(str(path), as_attachment=True,
                         download_name=f"Job_Leads_{stamp}{suffix}.xlsx")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ──────────────────────────────────────────────────────────────────────────────
# API — CUSTOM AI GENERATORS FOR JOBS
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/generate/cover-letter", methods=["POST"])
def api_gen_cover_letter():
    """Generate custom cover letter for a specific job."""
    data = request.get_json(force=True) or {}
    job = resolve_job_payload(data.get("job", {}), data.get("job_ref"))
    if not job:
        return jsonify({"ok": False, "msg": "Job not found for cover letter generation."}), 404

    from app.ai.scorer import generate_custom_cover_letter
    result = generate_custom_cover_letter(job)
    return jsonify({"ok": True, "output": result})


@app.route("/api/generate/resume", methods=["POST"])
def api_gen_resume():
    """Generate customized resume summary for a job."""
    data = request.get_json(force=True) or {}
    job = resolve_job_payload(data.get("job", {}), data.get("job_ref"))
    if not job:
        return jsonify({"ok": False, "msg": "Job not found for resume generation."}), 404

    from app.ai.scorer import generate_custom_resume
    result = generate_custom_resume(job)
    return jsonify({"ok": True, "output": result})


@app.route("/api/generate/resume-bullets", methods=["POST"])
def api_gen_resume_bullets():
    """Generate custom ATS resume bullet points for a job."""
    data = request.get_json(force=True) or {}
    job = resolve_job_payload(data.get("job", {}), data.get("job_ref"))
    if not job:
        return jsonify({"ok": False, "msg": "Job not found."}), 404

    from app.ai.free_ai import generate_tailored_resume_bullets
    result = generate_tailored_resume_bullets(job)
    return jsonify({"ok": True, "output": result})


@app.route("/api/generate/negotiation-tips", methods=["POST"])
def api_gen_negotiation():
    """Generate compensation and salary negotiation strategy for a job."""
    data = request.get_json(force=True) or {}
    job = resolve_job_payload(data.get("job", {}), data.get("job_ref"))
    if not job:
        return jsonify({"ok": False, "msg": "Job not found."}), 404

    from app.ai.free_ai import generate_salary_negotiation_tips
    result = generate_salary_negotiation_tips(job)
    return jsonify({"ok": True, "output": result})


@app.route("/api/generate/interview-prep", methods=["POST"])
def api_gen_interview():
    """Generate interview preparation for a specific job."""
    data = request.get_json(force=True) or {}
    job = resolve_job_payload(data.get("job", {}), data.get("job_ref"))
    if not job:
        return jsonify({"ok": False, "msg": "Job not found for interview prep."}), 404

    from app.ai.scorer import generate_interview_suggestions
    result = generate_interview_suggestions(job)
    return jsonify({"ok": True, "output": result})


@app.route("/api/generate/gap-analysis", methods=["POST"])
def api_gen_gap():
    """Generate skill gap analysis for a specific job."""
    data = request.get_json(force=True) or {}
    job = resolve_job_payload(data.get("job", {}), data.get("job_ref"))
    if not job:
        return jsonify({"ok": False, "msg": "Job not found for gap analysis."}), 404

    from app.ai.scorer import generate_job_specific_gap
    result = generate_job_specific_gap(job)
    return jsonify({"ok": True, "output": result})


@app.route("/api/generate/job-tools", methods=["POST"])
def api_gen_job_tools():
    """Generate multiple job-specific AI outputs from a Job ID."""
    data = request.get_json(force=True) or {}
    job = resolve_job_payload(data.get("job", {}), data.get("job_ref"))
    if not job:
        return jsonify({"ok": False, "msg": "Job not found for job tools generation."}), 404

    from app.ai.scorer import (
        generate_custom_cover_letter,
        generate_custom_resume,
        generate_job_specific_gap,
        generate_job_reason,
        generate_missing_areas,
    )

    cover_letter = generate_custom_cover_letter(job)
    resume        = generate_custom_resume(job)
    gap_analysis  = generate_job_specific_gap(job)
    reason        = generate_job_reason(job)
    missing_areas = generate_missing_areas(job)

    return jsonify({
        "ok": True,
        "cover_letter": cover_letter,
        "resume": resume,
        "gap_analysis": gap_analysis,
        "reason": reason,
        "missing_areas": missing_areas,
    })

# ──────────────────────────────────────────────────────────────────────────────
# API — OUTREACH AGENT (networking / recruiter outreach)
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/generate/outreach", methods=["POST"])
def api_gen_outreach():
    """Generate a full outreach pack (connection note, InMail, email, follow-up)
    for a specific job, optionally personalised with a recruiter name."""
    data = request.get_json(force=True) or {}
    job = resolve_job_payload(data.get("job", {}), data.get("job_ref"))
    if not job:
        return jsonify({"ok": False, "msg": "Job not found for outreach generation."}), 404

    recruiter_name = data.get("recruiter_name", "") or job.get("Recruiter Name", "")

    from app.outreach.outreach_service import generate_outreach_pack
    pack = generate_outreach_pack(job, recruiter_name)

    return jsonify({"ok": True, **pack})


@app.route("/api/outreach/queue")
def api_outreach_queue():
    """List of top job leads that are good outreach candidates
    (high AI score, not yet marked Sent/Replied)."""
    try:
        min_score = float(request.args.get("min_score", 50))
        limit     = int(request.args.get("limit", 50))

        jobs_json = load_jobs_json()
        if jobs_json:
            candidates = [
                j for j in jobs_json.values()
                if float(j.get("AI Score") or 0) >= min_score
                and str(j.get("Outreach Status", "")).strip() not in ("Sent", "Replied")
            ]
            candidates.sort(key=lambda j: float(j.get("AI Score") or 0), reverse=True)
            return jsonify({"ok": True, "jobs": candidates[:limit]})

        db = SessionLocal()
        try:
            jobs = get_outreach_queue(db, minimum_score=min_score, limit=limit)
            return jsonify({"ok": True, "jobs": [job_to_dict(j) for j in jobs]})
        finally:
            db.close()
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


# ──────────────────────────────────────────────────────────────────────────────
# API — APPLICANT PROFILE  (answers the auto-apply bot uses for required fields)
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/profile/catalog")
def api_profile_catalog():
    """Grouped catalog of every field the auto-apply bot can fill in,
    used to render the dynamic Applicant Profile form."""
    from app.config.required_fields import FIELD_GROUPS
    return jsonify({"ok": True, "groups": FIELD_GROUPS})


@app.route("/api/profile", methods=["GET", "POST"])
def api_profile():
    from app.services.profile_service import load_profile, save_profile, profile_completeness

    if request.method == "GET":
        profile = load_profile()
        return jsonify({"ok": True, "profile": profile, "completeness": profile_completeness(profile)})

    data = request.get_json(force=True) or {}
    profile = save_profile(data)
    return jsonify({"ok": True, "profile": profile, "completeness": profile_completeness(profile)})


@app.route("/api/question-bank", methods=["GET", "POST"])
def api_question_bank():
    """List captured portal questions or add/update one from the portal UI."""
    from app.services.profile_service import load_question_bank, save_question_answer

    if request.method == "GET":
        return jsonify({"ok": True, "questions": load_question_bank()})

    data = request.get_json(force=True) or {}
    question = str(data.get("question", "")).strip()
    if not question:
        return jsonify({"ok": False, "msg": "Question is required."}), 400
    entry = save_question_answer(question, data.get("answer", ""), data.get("source", "manual"))
    return jsonify({"ok": True, "question": entry})


@app.route("/api/question-bank/<path:question_id>", methods=["PUT"])
def api_question_bank_update(question_id):
    from app.services.profile_service import update_question_answer

    data = request.get_json(force=True) or {}
    entry = update_question_answer(question_id, data.get("answer", ""))
    if entry is None:
        return jsonify({"ok": False, "msg": "Question not found."}), 404
    return jsonify({"ok": True, "question": entry})


@app.route("/api/apply/required-fields")
def api_apply_required_fields():
    """For a given job (job_ref = Job ID / DB id / URL), detect which
    application-form fields it likely requires (via title+description
    keyword matching) and report which ones the saved profile can already
    answer vs. which are still missing."""
    from app.config.required_fields import detect_required_fields, field_coverage, ALL_FIELDS
    from app.services.profile_service import load_profile

    job_ref = request.args.get("job_ref", "")
    job = resolve_job_payload({}, job_ref)
    if not job:
        return jsonify({"ok": False, "msg": "Job not found"}), 404

    text = " ".join(str(job.get(k, "")) for k in
                     ("Title", "Job Description", "Key Skills", "Job Highlights", "Role"))
    field_keys = detect_required_fields(text) or [
        "name", "email", "phone", "current_location", "total_experience",
        "current_ctc", "expected_ctc", "notice_period",
    ]

    profile = load_profile()
    coverage = field_coverage(profile, field_keys)

    return jsonify({
        "ok": True,
        "job_title": job.get("Title", ""),
        "fields": [
            {**ALL_FIELDS[k], "value": profile.get(k, "")}
            for k in field_keys if k in ALL_FIELDS
        ],
        "filled_count": len(coverage["filled"]),
        "missing_count": len(coverage["missing"]),
        "missing_fields": coverage["missing"],
        "ready_to_apply": len(coverage["missing"]) == 0,
    })


@app.route("/api/apply/required-fields/bulk", methods=["POST"])
def api_apply_required_fields_bulk():
    """Same detection as above, run across a batch of jobs (e.g. all 'New'
    jobs) so the UI can show a readiness checklist before bulk auto-apply."""
    from app.config.required_fields import detect_required_fields, field_coverage
    from app.services.profile_service import load_profile

    data = request.get_json(force=True) or {}
    job_refs = data.get("job_refs") or []
    jobs_json = load_jobs_json()

    if not job_refs:
        candidates = [j for j in jobs_json.values() if str(j.get("Status", "New")).lower() == "new"]
    else:
        wanted = {str(r).strip().lower() for r in job_refs}
        candidates = [
            j for j in jobs_json.values()
            if str(j.get("Job ID", "")).lower() in wanted or str(j.get("URL", "")).lower() in wanted
        ]

    profile = load_profile()
    results = []
    for job in candidates:
        text = " ".join(str(job.get(k, "")) for k in ("Title", "Job Description", "Key Skills"))
        field_keys = detect_required_fields(text) or ["name", "email", "phone"]
        coverage = field_coverage(profile, field_keys)
        results.append({
            "job_id": job.get("Job ID", ""),
            "title": job.get("Title", ""),
            "company": job.get("Company", ""),
            "missing_fields": coverage["missing"],
            "ready_to_apply": len(coverage["missing"]) == 0,
        })

    return jsonify({
        "ok": True,
        "total": len(results),
        "ready": len([r for r in results if r["ready_to_apply"]]),
        "not_ready": len([r for r in results if not r["ready_to_apply"]]),
        "jobs": results,
    })


# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import socket
    local_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    print()
    print("=" * 55)
    print("  BABLU JOB AGENT  —  AI-Powered Job Hunter")
    print("=" * 55)
    print("  🖥️  Desktop:  http://127.0.0.1:5000")
    print(f"  📱  Mobile:   http://{local_ip}:5000 (same Wi-Fi)")
    print("  🛑  Stop:     Ctrl+C")
    print("=" * 55)
    import os
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting Flask server on port {port} (0.0.0.0)...", flush=True)
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)
