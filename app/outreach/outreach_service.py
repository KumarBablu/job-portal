"""
Outreach Agent
==============
Generates personalised networking / outreach content for a job lead:

- LinkedIn connection request note   (<= 300 characters, LinkedIn's hard limit)
- LinkedIn InMail / DM message        (longer, pitch-style message)
- Cold outreach email                 (subject + body, for recruiter/HR email)
- Follow-up nudge                     (short, polite check-in message)

All copy is generated locally from clean, editable templates filled in with
the job/candidate data, so this feature always works out of the box with
zero external setup and no third-party AI service.
"""

from __future__ import annotations

from typing import Dict

from app.ai.scorer import ai_or_fallback, safe_get, truncate

# ──────────────────────────────────────────────────────────────────────────────
# CANDIDATE (kept in sync with CANDIDATE in run.py / app/ai/scorer.py)
# ──────────────────────────────────────────────────────────────────────────────
CANDIDATE_NAME       = "Bablu Kumar"
CANDIDATE_TITLE      = "Data Analyst"
CANDIDATE_EXPERIENCE = "3+ years"
CANDIDATE_SKILLS     = "SQL, Python, Power BI, Pandas, ETL, Dashboarding, Reporting, Excel"


def _job_fields(job: Dict):
    """Pull the common fields out of a job dict, regardless of key casing."""
    title   = safe_get(job, "Title", "title", default="this role")
    company = safe_get(job, "Company", "company", default="your team")
    location= safe_get(job, "Location", "location", default="")
    skills  = safe_get(job, "Key Skills", "key_skills", default=CANDIDATE_SKILLS)
    desc    = truncate(safe_get(job, "Listing", "Job Description", "description"), 500)
    return title, company, location, skills, desc


# ──────────────────────────────────────────────────────────────────────────────
# LINKEDIN CONNECTION NOTE  (hard limit: 300 characters)
# ──────────────────────────────────────────────────────────────────────────────

def generate_connection_note(job: Dict, recruiter_name: str = "") -> str:
    """Short LinkedIn connection request note. Always kept under 300 chars."""

    title, company, _, _, _ = _job_fields(job)
    who = f"Hi {recruiter_name.split()[0]}," if recruiter_name.strip() else "Hi,"

    def fallback():
        note = (
            f"{who} I'm {CANDIDATE_NAME}, a {CANDIDATE_TITLE} ({CANDIDATE_EXPERIENCE}) "
            f"interested in the {title} role at {company}. Would love to connect and "
            f"learn more about the team!"
        )
        return note[:300]

    return fallback()


# ──────────────────────────────────────────────────────────────────────────────
# LINKEDIN INMAIL / DM
# ──────────────────────────────────────────────────────────────────────────────

def generate_linkedin_inmail(job: Dict, recruiter_name: str = "") -> str:
    """Slightly longer LinkedIn DM/InMail pitch, sent after connecting."""

    title, company, location, skills, desc = _job_fields(job)
    greeting = f"Hi {recruiter_name.split()[0]}," if recruiter_name.strip() else "Hi,"

    def fallback():
        return f"""{greeting}

I hope you're doing well! I came across the {title} opening at {company}{f' ({location})' if location else ''} and wanted to reach out directly.

I'm {CANDIDATE_NAME}, a {CANDIDATE_TITLE} with {CANDIDATE_EXPERIENCE} of experience in {skills}. I believe my background lines up well with what you're looking for, and I'd welcome the chance to discuss how I could contribute to your team.

Would you be open to a quick chat this week? Happy to share my resume or portfolio if useful.

Best regards,
{CANDIDATE_NAME}"""

    prompt = f"""
Write a short, friendly LinkedIn InMail/DM (120-180 words) from {CANDIDATE_NAME},
a {CANDIDATE_TITLE} with {CANDIDATE_EXPERIENCE} experience in {skills},
to a recruiter/hiring manager{f' named {recruiter_name}' if recruiter_name.strip() else ''}
about the "{title}" role at {company}.
Job context: {desc}
Tone: warm, confident, not desperate. End with a soft call to action (quick chat / resume).
Return ONLY the message body.
"""

    return ai_or_fallback(prompt, fallback)


# ──────────────────────────────────────────────────────────────────────────────
# COLD OUTREACH EMAIL  (subject + body)
# ──────────────────────────────────────────────────────────────────────────────

def generate_outreach_email(job: Dict, recruiter_name: str = "") -> Dict[str, str]:
    """Cold email to a recruiter / hiring manager. Returns {subject, body}."""

    title, company, location, skills, desc = _job_fields(job)
    greeting = f"Dear {recruiter_name}," if recruiter_name.strip() else "Hello,"
    subject  = f"Application Interest — {title} at {company}"

    def fallback_body():
        return f"""{greeting}

My name is {CANDIDATE_NAME}, and I'm reaching out regarding the {title} position at {company}{f' in {location}' if location else ''}.

With {CANDIDATE_EXPERIENCE} of experience in {skills}, I'm confident I can bring immediate value to your team — from building reporting dashboards to driving data-informed decisions.

I've attached my resume for your review and would welcome the opportunity to discuss how my background fits your requirements. Please let me know a convenient time to connect.

Thank you for your time and consideration.

Best regards,
{CANDIDATE_NAME}"""

    prompt = f"""
Write a professional cold outreach email body (150-220 words) from {CANDIDATE_NAME},
a {CANDIDATE_TITLE} with {CANDIDATE_EXPERIENCE} experience in {skills},
to a recruiter/hiring manager{f' named {recruiter_name}' if recruiter_name.strip() else ''}
about the "{title}" role at {company}.
Job context: {desc}
Structure: greeting, why I'm reaching out, 1-2 lines on fit, polite call to action, sign-off.
Return ONLY the email body (no subject line).
"""

    body = ai_or_fallback(prompt, fallback_body)
    return {"subject": subject, "body": body}


# ──────────────────────────────────────────────────────────────────────────────
# FOLLOW-UP / NUDGE MESSAGE
# ──────────────────────────────────────────────────────────────────────────────

def generate_followup_message(job: Dict, recruiter_name: str = "") -> str:
    """Short polite follow-up for when there's been no reply."""

    title, company, _, _, _ = _job_fields(job)
    greeting = f"Hi {recruiter_name.split()[0]}," if recruiter_name.strip() else "Hi,"

    def fallback():
        return (
            f"{greeting} just following up on my earlier note about the {title} role "
            f"at {company}. I'm still very interested and happy to share any more "
            f"information that's helpful. Thanks so much for your time!"
        )

    prompt = f"""
Write a short, polite follow-up message (40-70 words) from {CANDIDATE_NAME} checking in
about the "{title}" role at {company}, since there has been no reply to an earlier
outreach message{f' to {recruiter_name}' if recruiter_name.strip() else ''}.
Tone: courteous, brief, not pushy. Return ONLY the message text.
"""

    return ai_or_fallback(prompt, fallback)


# ──────────────────────────────────────────────────────────────────────────────
# FULL OUTREACH PACK — all four assets in one call
# ──────────────────────────────────────────────────────────────────────────────

def generate_outreach_pack(job: Dict, recruiter_name: str = "") -> Dict[str, str]:
    """Generate the full outreach pack for a job in one shot using Free AI."""
    from app.ai.free_ai import generate_recruiter_outreach_pack
    return generate_recruiter_outreach_pack(job, recruiter_name)
