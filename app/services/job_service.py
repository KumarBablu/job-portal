from sqlalchemy.orm import Session
from app.models.job_model import Job


# ──────────────────────────────────────────────────────────────────────────────
# CREATE / UPSERT
# ──────────────────────────────────────────────────────────────────────────────

def upsert_job(db: Session, job_data: dict, commit: bool = True) -> Job:
    """Insert a new job (keyed by url), or refresh a re-scraped job's
    listing/score fields in place. User-tracked fields (status, notes,
    contact, interview/follow-up dates, outreach, external-portal info)
    are only set on first insert and are never overwritten by a re-scrape."""
    url = job_data.get("url", "") or job_data.get("URL", "")
    if not url:
        return None

    existing = db.query(Job).filter(Job.url == url).first()

    fresh_fields = dict(
        title          = job_data.get("title") or job_data.get("Title", ""),
        company        = job_data.get("company") or job_data.get("Company", ""),
        location       = job_data.get("location") or job_data.get("Location", ""),
        salary         = job_data.get("salary") or job_data.get("Salary", ""),
        experience     = job_data.get("experience") or job_data.get("Experience", ""),
        source         = job_data.get("source") or job_data.get("Platform", ""),
        description    = job_data.get("description") or job_data.get("Job Description") or "",
        key_skills     = job_data.get("key_skills") or job_data.get("Key Skills") or job_data.get("KeySkills") or "",
        job_highlights = job_data.get("job_highlights") or job_data.get("Job Highlights") or "",
        role           = job_data.get("role") or job_data.get("Role") or "",
        industry_type  = job_data.get("industry_type") or job_data.get("Industry Type") or "",
        department     = job_data.get("department") or job_data.get("Department") or "",
        employment_type= job_data.get("employment_type") or job_data.get("Employment Type") or "",
        education      = job_data.get("education") or job_data.get("Education") or "",
        listing        = job_data.get("listing") or job_data.get("Listing", ""),
        keyword        = job_data.get("keyword") or job_data.get("Keyword", ""),
        match_score    = float(job_data.get("match_score") or job_data.get("Match Score") or 0),
        ai_score       = float(job_data.get("ai_score") or job_data.get("AI Score") or 0),
        ai_reason      = job_data.get("ai_reason") or job_data.get("AI Reason", ""),
        scraped_at     = job_data.get("scraped_at") or job_data.get("Scraped At", ""),
    )

    if existing:
        detail_columns = {
            "description", "key_skills", "job_highlights", "role",
            "industry_type", "department", "employment_type", "education",
        }
        for key, val in fresh_fields.items():
            if key in detail_columns and not str(val or "").strip():
                continue
            setattr(existing, key, val)
        if commit:
            db.commit()
            db.refresh(existing)
        return existing

    job = Job(
        job_id    = job_data.get("job_id") or job_data.get("Job ID", ""),
        url       = url,
        unique_id = job_data.get("unique_id") or job_data.get("Unique ID", ""),
        status    = job_data.get("status") or job_data.get("Status", "New"),
        **fresh_fields,
    )
    db.add(job)
    if commit:
        db.commit()
        db.refresh(job)
    return job


def upsert_jobs_batch(db: Session, jobs_dict: dict) -> int:
    """Batch-upsert many jobs with a single commit. 100x faster than individual commits."""
    if not jobs_dict:
        return 0
    count = 0
    for jid, job in jobs_dict.items():
        try:
            payload = {
                "job_id":       jid,
                "title":        job.get("Title", ""),
                "company":      job.get("Company", ""),
                "location":     job.get("Location", ""),
                "salary":       job.get("Salary", ""),
                "experience":   job.get("Experience", ""),
                "source":       job.get("Platform", ""),
                "url":          job.get("URL", ""),
                "listing":      job.get("Listing", ""),
                "keyword":      job.get("Keyword", ""),
                "match_score":  float(job.get("Match Score") or 0),
                "ai_score":     float(job.get("AI Score") or 0),
                "ai_reason":    job.get("AI Reason", ""),
                "status":       job.get("Status", "New"),
                "scraped_at":   job.get("Scraped At", ""),
                "applied_date": job.get("Applied Date", ""),
                "contact":      job.get("Contact", ""),
                "interview_date": job.get("Interview Date", ""),
                "follow_up":    job.get("Follow-Up", ""),
                "result":       job.get("Result", ""),
                "cover_letter": job.get("Cover Letter", ""),
                "notes":        job.get("Notes", ""),
                "description":  job.get("Job Description", "") or job.get("description", ""),
                "key_skills":   job.get("Key Skills", "") or job.get("key_skills", ""),
                "job_highlights": job.get("Job Highlights", "") or job.get("job_highlights", ""),
                "role":         job.get("Role", "") or job.get("role", ""),
                "industry_type": job.get("Industry Type", "") or job.get("industry_type", ""),
                "department":   job.get("Department", "") or job.get("department", ""),
                "education":    job.get("Education", "") or job.get("education", ""),
            }
            upsert_job(db, payload, commit=False)
            count += 1
        except Exception:
            pass
    db.commit()
    return count


# ──────────────────────────────────────────────────────────────────────────────

def get_all_jobs(db: Session):
    return db.query(Job).order_by(Job.ai_score.desc()).all()


def get_job_by_id(db: Session, job_id: int) -> Job:
    return db.query(Job).filter(Job.id == job_id).first()


def get_high_match_jobs(db: Session, minimum_score: float = 60.0):
    return db.query(Job).filter(Job.ai_score >= minimum_score).all()


def get_outreach_queue(db: Session, minimum_score: float = 50.0, limit: int = 50):
    """Jobs worth reaching out about: good match score, not already fully worked (Sent/Replied)."""
    return (
        db.query(Job)
        .filter(Job.ai_score >= minimum_score)
        .filter(Job.outreach_status.notin_(["Sent", "Replied"]))
        .order_by(Job.ai_score.desc())
        .limit(limit)
        .all()
    )


# ──────────────────────────────────────────────────────────────────────────────
# UPDATE
# ──────────────────────────────────────────────────────────────────────────────

def update_job(db: Session, job_id: int, data: dict) -> Job:
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        return None
    for key, val in data.items():
        if hasattr(job, key):
            setattr(job, key, val)
    db.commit()
    db.refresh(job)
    return job


# ──────────────────────────────────────────────────────────────────────────────
# DELETE
# ──────────────────────────────────────────────────────────────────────────────

def delete_job(db: Session, job_id: int) -> bool:
    """Delete a single job by numeric ID."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        return False
    db.delete(job)
    db.commit()
    return True


def delete_jobs_by_ids(db: Session, refs: list) -> int:
    """Delete jobs matching any of the given job_id/url values.
    Returns the count of deleted jobs."""
    if not refs:
        return 0
    ref_strs = [str(r).strip() for r in refs if r is not None and str(r).strip()]
    if not ref_strs:
        return 0

    from sqlalchemy import or_
    conditions = [Job.job_id.in_(ref_strs), Job.url.in_(ref_strs)]
    int_ids = []
    for r in ref_strs:
        try:
            int_ids.append(int(r))
        except ValueError:
            pass
    if int_ids:
        conditions.append(Job.id.in_(int_ids))

    to_delete = db.query(Job).filter(or_(*conditions)).all()
    count = len(to_delete)
    for j in to_delete:
        db.delete(j)
    db.commit()
    return count


def delete_jobs_by_filter(db: Session, filters: dict) -> int:
    """Delete jobs matching the specified filter criteria.
    Returns the count of deleted jobs.
    
    Filters can include:
    - status: job status (e.g. 'New', 'Applied', 'Rejected')
    - source/platform: job source/platform
    - ai_score_min: minimum AI score
    - ai_score_max: maximum AI score
    - location: location string (substring match)
    - company: company name (substring match)
    """
    query = db.query(Job)
    
    if filters.get("status"):
        query = query.filter(Job.status == filters["status"])
    if filters.get("source"):
        query = query.filter(Job.source == filters["source"])
    if filters.get("platform"):
        query = query.filter(Job.source == filters["platform"])
    if filters.get("ai_score_min"):
        query = query.filter(Job.ai_score >= filters["ai_score_min"])
    if filters.get("ai_score_max"):
        query = query.filter(Job.ai_score <= filters["ai_score_max"])
    if filters.get("location"):
        location_filter = f"%{filters['location']}%"
        query = query.filter(Job.location.ilike(location_filter))
    if filters.get("company"):
        company_filter = f"%{filters['company']}%"
        query = query.filter(Job.company.ilike(company_filter))
    
    jobs_to_delete = query.all()
    count = len(jobs_to_delete)
    
    for job in jobs_to_delete:
        db.delete(job)
    db.commit()
    
    return count


# ──────────────────────────────────────────────────────────────────────────────



def get_external_portal_jobs(db: Session):
    """Jobs redirected to an external ATS/company site during auto-apply —
    these need a manual follow-up since the bot intentionally skips them."""
    return (
        db.query(Job)
        .filter(Job.external_portal == "True")
        .order_by(Job.ai_score.desc())
        .all()
    )


def get_jobs_by_ids(db: Session, refs: list):
    """Fetch jobs matching any of the given job_id/url values."""
    normalized = {str(r).strip().lower() for r in (refs or []) if r is not None}
    if not normalized:
        return []
    jobs = db.query(Job).all()
    return [
        j for j in jobs
        if str(j.job_id or "").strip().lower() in normalized
        or str(j.url or "").strip().lower() in normalized
    ]


# ──────────────────────────────────────────────────────────────────────────────
# STATS
# ──────────────────────────────────────────────────────────────────────────────

def get_stats(db: Session) -> dict:
    jobs = get_all_jobs(db)
    by_platform = {}
    for j in jobs:
        p = j.source or "Unknown"
        by_platform[p] = by_platform.get(p, 0) + 1

    return {
        "total":      len(jobs),
        "top":        len([j for j in jobs if (j.ai_score or j.match_score or 0) >= 60]),
        "applied":    len([j for j in jobs if j.status == "Applied"]),
        "already_applied": len([j for j in jobs if j.status == "Already Applied"]),
        "interviews": len([j for j in jobs if j.status == "Interviewing"]),
        "offers":     len([j for j in jobs if j.status == "Offer"]),
        "rejected":   len([j for j in jobs if j.status == "Rejected"]),
        "external_portal": len([j for j in jobs if j.external_portal == "True"]),
        "errors":     len([j for j in jobs if j.status == "Error"]),
        "new":        len([j for j in jobs if (j.status or "New") == "New"]),
        "by_platform": by_platform,
        "outreach_sent":     len([j for j in jobs if j.outreach_status == "Sent"]),
        "outreach_replied":  len([j for j in jobs if j.outreach_status == "Replied"]),
        "outreach_drafted":  len([j for j in jobs if j.outreach_status == "Drafted"]),
    }
