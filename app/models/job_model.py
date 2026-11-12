from sqlalchemy import Column, Integer, String, Text, Float, DateTime
from sqlalchemy.sql import func
from app.database.db import Base


class Job(Base):
    __tablename__ = "jobs"

    id            = Column(Integer, primary_key=True, index=True)
    job_id        = Column(String, unique=True, index=True)   # platform job ID
    title         = Column(String, nullable=False)
    company       = Column(String, nullable=False)
    location      = Column(String, default="")
    salary        = Column(String, default="")
    experience    = Column(String, default="")
    job_type      = Column(String, default="Full Time")
    source        = Column(String, default="")   # LinkedIn / Naukri / etc
    url           = Column(Text, unique=True)
    description   = Column(Text, default="")
    key_skills    = Column(Text, default="")
    job_highlights= Column(Text, default="")
    role          = Column(String, default="")
    industry_type = Column(String, default="")
    department    = Column(String, default="")
    employment_type = Column(String, default="")
    education     = Column(String, default="")
    listing       = Column(Text, default="")
    keyword       = Column(String, default="")
    unique_id     = Column(String, index=True, default="")

    match_score   = Column(Float, default=0)
    ai_score      = Column(Float, default=0)
    ai_reason     = Column(String, default="")

    status        = Column(String, default="New")
    applied_date  = Column(String, default="")
    contact       = Column(String, default="")
    interview_date= Column(String, default="")
    follow_up     = Column(String, default="")
    result        = Column(String, default="")
    cover_letter  = Column(Text, default="")
    notes         = Column(Text, default="")

    # ── External-portal tracking (redirect-and-skip auto-apply flow) ──────────
    external_portal  = Column(String, default="")   # "True" once redirected off-platform
    external_ats     = Column(String, default="")   # detected ATS name (lever/workday/greenhouse/...)
    external_domain  = Column(String, default="")   # redirect target domain
    redirect_url     = Column(Text, default="")      # full redirect URL for manual completion

    # ── Outreach Agent fields (networking / recruiter outreach tracking) ──────
    outreach_status        = Column(String, default="Not Started")  # Not Started/Drafted/Sent/Replied/No Response
    outreach_channel        = Column(String, default="LinkedIn")     # LinkedIn/Email/Naukri/Referral
    recruiter_name          = Column(String, default="")
    recruiter_contact       = Column(String, default="")             # email or LinkedIn profile URL
    outreach_message        = Column(Text, default="")
    outreach_sent_date      = Column(String, default="")
    outreach_followup_date  = Column(String, default="")
    outreach_notes          = Column(Text, default="")

    scraped_at    = Column(String, default="")
    created_at    = Column(DateTime, server_default=func.now())
