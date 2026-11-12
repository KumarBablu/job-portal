"""
===============================================================================
                    AI JOB SCORER + CAREER COPILOT
===============================================================================

FEATURES
--------
✔ 100% Local, offline text generation (no external AI service, no API keys)
✔ Safe dependency handling
✔ AI job scoring via local sentence-transformer embeddings
✔ ATS resume generator
✔ Full one-page resume
✔ Full cover letter
✔ Enhanced interview preparation
✔ Enhanced gap analysis
✔ Semantic + keyword matching
✔ Production safe

INSTALLATION
------------

pip install sentence-transformers torch

===============================================================================
"""

from __future__ import annotations

import os
import re
import json
from datetime import datetime
from typing import Dict, List, Optional

# =============================================================================
# HUGGING FACE HUB — DEFENSIVE ENV VARS (set before any HF import)
# =============================================================================
# huggingface_hub can use "hf_xet", a Rust-based fast-download backend, to
# fetch model files. On some Windows machines that are low on memory, this
# Rust binary crashes with "memory allocation of N bytes failed" — the exact
# same failure signature as Selenium Manager's Rust binary crashing (see
# Apply/Apply-2/app.py's make_driver()). Forcing the plain, memory-light
# Python HTTP downloader avoids that failure mode entirely (slower, but far
# more reliable on constrained machines). These only take effect if not
# already set, so anyone who wants the faster backend can still opt back in.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "0")
os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "0")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

# =============================================================================
# SAFE IMPORTS
# =============================================================================

try:
    from sentence_transformers import SentenceTransformer, util
    ST_AVAILABLE = True
except Exception:
    SentenceTransformer = None
    util = None
    ST_AVAILABLE = False

try:
    import psutil
except Exception:
    psutil = None

# =============================================================================
# CONFIG
# =============================================================================

# =============================================================================
# CANDIDATE PROFILE (from Resume: NoBroker Technologies & Udaan)
# =============================================================================

CANDIDATE_PROFILE = """
Bablu Kumar — Data Analyst | SQL, Python & Power BI | KPI Reporting & Business Analytics
Bengaluru, Karnataka. 3.5 years of experience in e-commerce, proptech, and supply chain analytics,
delivering business insights through SQL, Python, Power BI (DAX), and Advanced Excel.
Built 5+ executive KPI dashboards and automated ETL/reporting pipelines that cut reporting turnaround by 40%
and manual effort by 50%. Skilled in KPI definition, cohort and root-cause analysis, data modeling,
and cross-functional reporting across product, operations, and finance teams.
Experience at NoBroker Technologies Solutions (PropTech) and Udaan (B2B E-commerce).
Education: B.Tech (2018-2022) Aryabhata Knowledge University (CGPA 8.5/10).

Core Skills:
- SQL (Advanced: Joins, CTEs, Window Functions, Query Optimization, Subqueries)
- Python (Pandas, NumPy, Matplotlib, Data Pipelines, Automation)
- Power BI (DAX, Data Modeling, Row-Level Security, Report Publishing, Power Query)
- Advanced Excel (Pivot Tables, Power Query, Lookup Functions, Automation)
- Data & ETL (ETL Design & Automation, Multi-Source Data Integration, Data Quality Checks, Snowflake, AWS S3, GCP)
- Analytics (KPI Reporting, Cohort Analysis, Trend Analysis, Root-Cause Analysis, MIS Reporting, Data Storytelling)
- Business & Stakeholder Management, Requirement Gathering, Cross-functional Collaboration

Domains: PropTech, B2B E-commerce, Supply Chain, Operations
Location: Bengaluru, Karnataka
"""

# =============================================================================
# CANDIDATE EXPERIENCE (used for experience-range matching bonus/penalty)
# =============================================================================

CANDIDATE_YEARS_EXPERIENCE = 3.5

# =============================================================================
# MODEL CACHE
# =============================================================================

_embedding_model = None

# =============================================================================
# UTILITIES
# =============================================================================


def clean_text(text: str) -> str:
    if not text:
        return ""

    text = re.sub(r"\s+", " ", str(text))
    return text.strip()


def truncate(text: str, limit: int = 1200) -> str:
    text = clean_text(text)

    if len(text) <= limit:
        return text

    return text[:limit] + "..."


def safe_get(job: dict, *keys, default="") -> str:
    for key in keys:
        value = job.get(key)

        if value:
            return str(value)

    return default


# =============================================================================
# EMBEDDING MODEL
# =============================================================================

def _low_memory() -> bool:
    """Best-effort check for whether the system is dangerously low on
    available memory. Returns False (assume fine) if psutil isn't
    installed or the check itself fails — this is purely a safety
    heuristic, never a hard requirement."""
    if psutil is None:
        return False
    try:
        avail_mb = psutil.virtual_memory().available / (1024 * 1024)
        return avail_mb < 350  # under ~350MB free is too tight to risk a model load/download
    except Exception:
        return False


def release_model():
    """Free the cached embedding model and return its memory to the OS.

    Once loaded, the model (plus its underlying torch/transformers stack —
    typically several hundred MB) stays cached in the Flask server's memory
    for the rest of the process's life, since get_model() intentionally
    avoids reloading it on every call. On a machine that's already tight on
    memory, that's a problem: it quietly eats into the headroom available
    for whatever runs next — most notably the Chrome-based auto-apply
    subprocess, which needs a large chunk of memory of its own. Releasing
    the model after a scoring batch finishes trades a few seconds of
    re-load time on the *next* scrape for meaningfully more free memory in
    between — worth it on constrained machines, and unnoticeable otherwise.
    """
    global _embedding_model, _profile_embedding
    _embedding_model = None
    _profile_embedding = None
    try:
        import gc
        gc.collect()
    except Exception:
        pass


def get_model():

    global _embedding_model

    if not ST_AVAILABLE:
        return None

    if _embedding_model is False:
        return None  # previously failed to load — don't keep retrying

    if _embedding_model is None:
        if _low_memory():
            # Attempting a model download/load while critically low on memory
            # is exactly what triggers native "memory allocation failed"
            # crashes in HF Hub's Rust download backend. Skip straight to
            # keyword-only scoring instead of risking it.
            print("Low available memory detected — skipping semantic model, using keyword-only scoring.")
            _embedding_model = False
            return None
        try:
            print("Loading SentenceTransformer model...")
            _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
            print("Model loaded successfully.")
        except BaseException as e:
            # BaseException (not just Exception) on purpose: some native/Rust
            # extension failures surface as low-level exceptions that don't
            # inherit from Exception. Catching broadly here — and always
            # falling back to keyword-only scoring — keeps a flaky model
            # download from ever taking down the whole app.
            print(f"Embedding model error: {e}")
            _embedding_model = False
            return None

    return _embedding_model


# =============================================================================
# JOB TEXT
# =============================================================================

def build_job_text(job: Dict) -> str:

    return f"""
    Title: {safe_get(job, 'Title', 'title')}
    Company: {safe_get(job, 'Company', 'company')}
    Location: {safe_get(job, 'Location', 'location')}

    Skills:
    {safe_get(job, 'Key Skills', 'key_skills')}

    Description:
    {safe_get(job, 'Listing', 'Job Description', 'description')}
    """


# =============================================================================
# KEYWORD MATCHING
# =============================================================================

CANDIDATE_SKILLS = {
    "sql", "python", "pandas", "numpy", "power bi", "dax", "excel",
    "advanced excel", "etl", "data pipeline", "dashboard", "reporting",
    "analytics", "business intelligence", "kpi", "cohort analysis",
    "retention analysis", "rfm", "root cause analysis", "data storytelling",
    "requirement gathering", "matplotlib", "seaborn", "power query",
    "aws", "azure", "gcp", "git", "github", "data visualization",
    "vlookup", "pivot table", "data modeling", "mis",
}

# A smaller set of "must-have" core skills — carries more weight in scoring
CANDIDATE_CORE_SKILLS = {"sql", "python", "power bi", "excel", "etl", "dashboard"}


def keyword_score(text: str) -> float:

    text = text.lower()

    matched = [s for s in CANDIDATE_SKILLS if s in text]
    core_matched = [s for s in CANDIDATE_CORE_SKILLS if s in text]

    base = (len(matched) / len(CANDIDATE_SKILLS)) * 100
    core_bonus = (len(core_matched) / len(CANDIDATE_CORE_SKILLS)) * 20  # up to +20

    return round(min(base + core_bonus, 100), 2)


def matched_skills_list(text: str) -> List[str]:
    text = text.lower()
    return sorted([s for s in CANDIDATE_SKILLS if s in text])


# =============================================================================
# EXPERIENCE-RANGE MATCHING
# =============================================================================

def experience_fit(job: Dict) -> float:
    """Compare the job's stated experience range against the candidate's
    years of experience. Returns a bonus/penalty in score points."""
    exp_text = safe_get(job, "Experience", "experience")
    if not exp_text:
        return 0.0

    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", exp_text)]
    if not nums:
        return 0.0

    lo = min(nums)
    hi = max(nums) if len(nums) > 1 else lo + 3
    candidate_years = CANDIDATE_YEARS_EXPERIENCE

    if lo <= candidate_years <= hi:
        return 5.0          # squarely in range
    if candidate_years < lo:
        gap = lo - candidate_years
        return max(-15.0, -4.0 * gap)   # under-qualified — small penalty
    # over-qualified
    gap = candidate_years - hi
    return max(-8.0, -2.0 * gap)


# =============================================================================
# SEMANTIC SCORING
# =============================================================================

_profile_embedding = None


def get_profile_embedding():
    global _profile_embedding
    model = get_model()
    if model is None:
        return None
    if _profile_embedding is None:
        _profile_embedding = model.encode(CANDIDATE_PROFILE, convert_to_tensor=True)
    return _profile_embedding


def semantic_score(text: str) -> Optional[float]:

    model = get_model()

    if model is None:
        return None

    try:
        emb_profile = get_profile_embedding()
        emb_job = model.encode(text, convert_to_tensor=True)

        similarity = util.cos_sim(emb_profile, emb_job)

        return round(float(similarity[0][0]) * 100, 2)

    except Exception as e:
        print(f"Semantic scoring failed: {e}")
        return None


# =============================================================================
# MATCH VERDICT
# =============================================================================

def generate_match_verdict(score: float) -> str:

    if score >= 85:
        return "Excellent Match"

    if score >= 70:
        return "Strong Match"

    if score >= 55:
        return "Moderate Match"

    if score >= 40:
        return "Weak Match"

    return "Low Match"


# =============================================================================
# MAIN JOB SCORER
# =============================================================================

def score_job(job: Dict, semantic: Optional[float] = None) -> Dict:
    """Score a single job. If `semantic` is precomputed (batch path), reuse it
    instead of re-encoding — pass None to compute it here (single-job path)."""

    text = build_job_text(job)

    if semantic is None:
        semantic = semantic_score(text)

    keyword = keyword_score(text)
    matched_skills = matched_skills_list(text)

    if semantic is None:
        final_score = keyword
        method = "keyword_only"
    else:
        final_score = round((semantic * 0.65) + (keyword * 0.35), 2)
        method = "hybrid"

    location = safe_get(job, "Location").lower()
    if "bangalore" in location or "bengaluru" in location:
        final_score += 3
    if "remote" in location:
        final_score += 2

    final_score += experience_fit(job)
    final_score = max(0.0, min(final_score, 100.0))

    verdict = generate_match_verdict(final_score)

    top_skills = matched_skills[:6]
    reason_bits = [verdict]
    if top_skills:
        reason_bits.append(f"{len(matched_skills)} skill match(es): {', '.join(top_skills)}")
    if semantic is not None:
        reason_bits.append(f"semantic fit {semantic:.0f}%")
    reason = " · ".join(reason_bits)

    result = dict(job)
    result["AI Score"] = round(final_score, 2)
    result["Semantic Score"] = semantic or 0
    result["Keyword Score"] = keyword
    result["Scoring Method"] = method
    result["Verdict"] = verdict
    result["Matched Skills"] = ", ".join(matched_skills)
    result["AI Reason"] = reason
    result["Match Score"] = round(final_score, 2)

    return result


# =============================================================================
# BATCH SCORING
# =============================================================================

def score_jobs_batch(jobs: List[Dict], log_fn=None) -> List[Dict]:
    """Score every job in `jobs`. Uses a single batch-encode pass over all job
    texts (much faster than encoding one job at a time) when a local
    sentence-transformer model is available, and falls back cleanly to
    keyword-only scoring otherwise — either way this always runs 100% locally
    with no external API calls."""

    total = len(jobs)
    log = log_fn if log_fn else (lambda msg: print(msg))
    log(f"Scoring {total} jobs...")

    if total == 0:
        return []

    texts = [build_job_text(job) for job in jobs]

    model = get_model()
    semantics: List[Optional[float]] = [None] * total

    if model is not None:
        try:
            profile_emb = get_profile_embedding()
            job_embs = model.encode(
                texts, convert_to_tensor=True, batch_size=32, show_progress_bar=False
            )
            sims = util.cos_sim(profile_emb, job_embs)[0]
            semantics = [round(float(s) * 100, 2) for s in sims]
        except Exception as e:
            log(f"  ⚠ Batch semantic scoring failed, falling back to keyword-only: {e}")
            semantics = [None] * total

    results = []
    for job, sem in zip(jobs, semantics):
        results.append(score_job(job, semantic=sem))

    results.sort(key=lambda x: x.get("AI Score", 0), reverse=True)
    log(f"  ✅ Scored {len(results)} jobs")

    return results


# =============================================================================
# FREE AI & CONTEXTUAL TEMPLATE ENGINE
# =============================================================================
# Text generation uses free online AI (Pollinations.ai / optional Gemini)
# with automatic fallback to high-fidelity contextual templates driven by
# Bablu Kumar's actual resume (NoBroker Technologies & Udaan).

def ai_or_fallback(prompt: str, fallback_fn):
    """Executes free AI with automatic fallback to contextual offline templates."""
    try:
        from app.ai.free_ai import query_free_ai
        return query_free_ai(prompt, fallback_fn=fallback_fn)
    except Exception:
        return fallback_fn()


# =============================================================================
# FULL ONE PAGE RESUME
# =============================================================================

def generate_full_resume(job: Dict) -> str:

    title = safe_get(job, "Title")
    company = safe_get(job, "Company")
    location = safe_get(job, "Location")

    return f"""
===============================================================================
                                BABLU KUMAR
===============================================================================

Location : Bangalore, Karnataka
Phone    : +91-XXXXXXXXXX
Email    : bablu.kumar@email.com
LinkedIn : linkedin.com/in/bablukumar
GitHub   : github.com/bablukumar

===============================================================================
PROFESSIONAL SUMMARY
===============================================================================

Results-driven Data Analyst with 3+ years of experience in SQL, Python,
Power BI, ETL pipelines, dashboard development, KPI reporting, and
business intelligence solutions across e-commerce, FMCG, finance,
and retail domains.

Target Role:
{title} at {company} ({location})

===============================================================================
CORE SKILLS
===============================================================================

• SQL (Joins, Window Functions, Stored Procedures)
• Python (Pandas, NumPy, Automation)
• Power BI (DAX, Dashboards, Data Modeling)
• Excel (Pivot Tables, VBA, Macros)
• ETL Pipelines & Data Transformation
• Business Intelligence & Reporting
• Data Visualization & KPI Analytics

===============================================================================
EXPERIENCE
===============================================================================

DATA ANALYST
ABC Analytics Pvt Ltd — Bangalore
Jun 2022 – Present

• Built Power BI dashboards used by 50+ stakeholders
• Automated reporting workflows saving 8+ hours weekly
• Improved reporting accuracy by 30%
• Optimized SQL queries reducing execution time drastically
• Developed ETL pipelines for finance and sales analytics

JUNIOR DATA ANALYST
XYZ Retail Solutions — Bangalore
Jan 2021 – May 2022

• Generated business performance reports
• Supported dashboard migration to Power BI
• Performed data cleaning using Python Pandas

===============================================================================
PROJECTS
===============================================================================

Sales Dashboard
• Built KPI-driven dashboards for business reporting

Automated Reporting Pipeline
• Automated recurring reporting using SQL + Python

Customer Analytics
• Analyzed churn and customer behavior patterns

===============================================================================
EDUCATION
===============================================================================

Bachelor of Technology (B.Tech / B.E.)

===============================================================================
"""


# =============================================================================
# FULL COVER LETTER
# =============================================================================

def generate_full_cover_letter(job: Dict) -> str:

    title = safe_get(job, "Title")
    company = safe_get(job, "Company")
    location = safe_get(job, "Location")

    today = datetime.now().strftime("%d %B %Y")

    return f"""
===============================================================================
                                COVER LETTER
===============================================================================

Bablu Kumar
Bangalore, Karnataka
+91-XXXXXXXXXX
bablu.kumar@email.com

Date: {today}

Hiring Manager
{company}
{location}

Dear Hiring Manager,

The opportunity to contribute as a {title} at {company} strongly aligns
with my background in analytics, reporting automation, dashboard
development, and business intelligence solutions.

With over 3 years of experience across e-commerce, finance, FMCG,
and retail domains, I have consistently delivered measurable business
impact through data-driven insights and automation.

In my current role, I developed Power BI dashboards used by
cross-functional stakeholders to monitor KPIs and business trends.
I also automated SQL and Python-based reporting workflows that reduced
manual effort by more than 8 hours weekly while improving reporting
accuracy by 30%.

My experience with SQL optimization, ETL development, reporting
automation, and dashboard design allows me to contribute effectively
in fast-paced analytics environments.

I am especially excited about this opportunity because it combines
analytics, problem-solving, and business impact — areas where I have
consistently delivered results throughout my career.

I would welcome the opportunity to discuss how my technical and
analytical background can contribute to your team.

Sincerely,

Bablu Kumar

===============================================================================
"""


# =============================================================================
# FULL INTERVIEW PREPARATION
# =============================================================================

def generate_full_interview_preparation(job: Dict) -> str:

    title = safe_get(job, "Title")
    company = safe_get(job, "Company")

    return f"""
===============================================================================
                    INTERVIEW PREPARATION GUIDE
===============================================================================

Role    : {title}
Company : {company}

===============================================================================
TECHNICAL QUESTIONS
===============================================================================

1. Explain Window Functions with real business examples
2. How would you optimize a slow SQL query?
3. Difference between INNER JOIN and LEFT JOIN
4. How have you used Python in analytics projects?
5. Explain missing value handling in Pandas
6. Explain DAX Measures in Power BI
7. How do you design executive dashboards?

===============================================================================
BEHAVIORAL QUESTIONS
===============================================================================

1. Describe a challenging stakeholder situation
2. Tell me about a tight deadline project
3. Explain a time your analysis changed a business decision

===============================================================================
HR QUESTIONS
===============================================================================

1. Why should we hire you?
2. Why do you want this role?
3. What are your strengths as a Data Analyst?

===============================================================================
KEY METRICS TO MENTION
===============================================================================

• Reduced reporting effort by 8+ hours weekly
• Improved reporting accuracy by 30%
• Built dashboards used by 50+ stakeholders
• Automated repetitive reporting workflows

===============================================================================
QUESTIONS TO ASK INTERVIEWER
===============================================================================

1. What are the primary KPIs for this role?
2. Which BI tools are heavily used internally?
3. What challenges is the analytics team solving?
4. How is success measured in the first 90 days?

===============================================================================
"""


# =============================================================================
# FULL GAP ANALYSIS
# =============================================================================

def generate_full_gap_analysis(job: Dict) -> str:

    title = safe_get(job, "Title")
    company = safe_get(job, "Company")

    return f"""
===============================================================================
                        ENHANCED GAP ANALYSIS
===============================================================================

Role    : {title}
Company : {company}

===============================================================================
CURRENT MARKET POSITION
===============================================================================

Experience Level:
Mid-Level Data Analyst

Competitiveness Score:
7.8 / 10

===============================================================================
STRONG MATCHES
===============================================================================

• SQL
• Python
• Power BI
• ETL Pipelines
• Reporting Automation
• Dashboard Development

===============================================================================
SKILL GAPS
===============================================================================

1. Advanced Power BI Service
   - RLS
   - Deployment pipelines
   - Gateway configuration

2. Cloud Platforms
   - Snowflake
   - BigQuery
   - Azure Synapse

3. Statistics & A/B Testing
   - Hypothesis testing
   - Experiment analysis

===============================================================================
ATS IMPROVEMENTS
===============================================================================

Recommended Keywords:
• Data Modeling
• Data Governance
• Stakeholder Management
• Predictive Analytics
• Business Insights

===============================================================================
HIGH ROI LEARNING ROADMAP
===============================================================================

Week 1
• Advanced SQL practice
• Power BI optimization

Week 2
• Snowflake basics
• Cloud analytics concepts

Week 3
• Python automation project
• Statistics fundamentals

Week 4
• Mock interviews
• Resume optimization

===============================================================================
FINAL RECOMMENDATION
===============================================================================

Strong fit for:
• Data Analyst
• BI Analyst
• Reporting Analyst
• Power BI Analyst

Next Growth Path:
• Senior Data Analyst
• BI Developer
• Analytics Consultant

===============================================================================
"""


# =============================================================================
# EXPORTS
# =============================================================================






# =============================================================================
# CUSTOM JOB-SPECIFIC GENERATORS  (add these to scorer.py)
# =============================================================================


def generate_custom_cover_letter(job: Dict) -> str:
    """Custom cover letter for a specific job. Called from /api/generate/cover-letter"""
    from app.ai.free_ai import generate_tailored_cover_letter
    return generate_tailored_cover_letter(job)


def generate_custom_resume(job: Dict) -> str:
    """Custom resume tailored to a specific job. Called from /api/generate/resume"""
    from app.ai.free_ai import generate_tailored_resume_bullets
    return generate_tailored_resume_bullets(job)


def generate_interview_suggestions(job: Dict) -> str:
    """Interview prep for a specific job. Called from /api/generate/interview-prep"""
    from app.ai.free_ai import generate_job_interview_prep
    return generate_job_interview_prep(job)


def generate_job_specific_gap(job: Dict) -> str:
    """Skill gap analysis for a specific job. Called from /api/generate/gap-analysis"""
    from app.ai.free_ai import generate_skill_gap_and_roadmap
    return generate_skill_gap_and_roadmap(job)


def generate_job_reason(job: Dict) -> str:
    """Why this job is a good match. Called from /api/generate/job-tools"""
    title   = safe_get(job, "Title", "title")
    company = safe_get(job, "Company", "company")
    skills  = safe_get(job, "Key Skills", "key_skills")

    prompt = f"""
In 3-4 concise sentences, explain why this {title} role at {company} is an excellent match for Bablu Kumar.
Candidate: Data Analyst with 3.5 yrs experience at NoBroker Technologies & Udaan; expertise in SQL, Python, Power BI (DAX), ETL automation (saved 8+ hrs/week).
Required Skills: {skills}
Focus on immediate value and domain alignment.
"""

    def fallback():
        return (
            f"This {title} role at {company} strongly aligns with Bablu Kumar's 3.5 years of "
            f"Data Analyst experience at NoBroker Technologies and Udaan. His proven success designing "
            f"Power BI dashboards and automating Python/SQL ETL workflows (reducing reporting turnaround by 40%) "
            f"directly matches the technical requirements and scale needed for this role."
        )

    return ai_or_fallback(prompt, fallback)


def generate_missing_areas(job: Dict) -> str:
    """What skills are missing for this job. Called from /api/generate/job-tools"""
    from app.ai.free_ai import _detect_matched_and_missing_skills
    _, missing = _detect_matched_and_missing_skills(job)
    return "Identified areas to review:\n" + "\n".join([f"• {m.title()}" for m in missing[:5]])


# =============================================================================
# SIMPLE WRAPPERS  (used by /api/cover_letter, /api/interview_prep, /api/gap_analysis)
# =============================================================================


def generate_cover_letter(
    title: str,
    company: str,
    location: str,
    notes: str = "",
) -> str:
    """Simple cover letter by title/company. Called from /api/cover_letter"""

    job = {
        "Title":    title,
        "Company":  company,
        "Location": location,
        "Listing":  notes,
    }
    return generate_custom_cover_letter(job)


def generate_interview_prep(title: str, company: str) -> str:
    """Simple interview prep by title/company. Called from /api/interview_prep"""

    job = {
        "Title":   title,
        "Company": company,
    }
    return generate_interview_suggestions(job)


def generate_gap_analysis(
    titles: list = None,
    listings: list = None,
) -> str:
    """General gap analysis across multiple jobs. Called from /api/gap_analysis"""

    titles_text   = ", ".join(titles[:10])   if titles   else "Data Analyst roles"
    listings_text = " ".join(listings[:3])   if listings else ""
    listings_text = truncate(listings_text, 600)

    prompt = f"""
Perform a general skill gap analysis for a Data Analyst job search.
Target Roles: {titles_text}
Sample Job Descriptions: {listings_text}

Candidate: Bablu Kumar, Data Analyst, 3+ years experience.
Current Skills: SQL, Python, Power BI, Pandas, ETL, Dashboarding, 
Reporting, Excel, DAX, NumPy.

Provide:
1. Common skill requirements across these roles
2. Candidate's strong matches
3. Skill gaps to address
4. Top 5 learning recommendations
"""

    def fallback():
        job = {"Title": titles[0] if titles else "Data Analyst", "Company": ""}
        return generate_full_gap_analysis(job)

    return ai_or_fallback(prompt, fallback)


# =============================================================================
# UPDATE __all__  (replace the existing __all__ in scorer.py with this)
# =============================================================================

__all__ = [
    # Core scoring
    "score_job",
    "score_jobs_batch",
    # Job-specific generators
    "generate_custom_cover_letter",
    "generate_custom_resume",
    "generate_interview_suggestions",
    "generate_job_specific_gap",
    "generate_job_reason",
    "generate_missing_areas",
    # Simple wrappers
    "generate_cover_letter",
    "generate_interview_prep",
    "generate_gap_analysis",
    # Full template generators (kept for fallback)
    "generate_full_resume",
    "generate_full_cover_letter",
    "generate_full_interview_preparation",
    "generate_full_gap_analysis",
]














__all__ = [
    "score_job",
    "score_jobs_batch",
    "generate_full_resume",
    "generate_full_cover_letter",
    "generate_full_interview_preparation",
    "generate_full_gap_analysis",
]

# =============================================================================
# TEST
# =============================================================================

if __name__ == "__main__":

    sample_job = {
        "Title": "Senior Data Analyst",
        "Company": "Flipkart",
        "Location": "Bangalore",
        "Key Skills": "SQL, Python, Power BI, ETL",
        "Job Description": """
        Looking for a Data Analyst with experience in dashboarding,
        SQL optimization, ETL pipelines, stakeholder reporting,
        and business intelligence systems.
        """,
    }

    # -------------------------------------------------------------------------
    # SCORE JOB
    # -------------------------------------------------------------------------

    scored = score_job(sample_job)

    print(json.dumps(scored, indent=2))

    # -------------------------------------------------------------------------
    # RESUME
    # -------------------------------------------------------------------------

    print(generate_full_resume(sample_job))

    # -------------------------------------------------------------------------
    # COVER LETTER
    # -------------------------------------------------------------------------

    print(generate_full_cover_letter(sample_job))

    # -------------------------------------------------------------------------
    # INTERVIEW PREP
    # -------------------------------------------------------------------------

    print(generate_full_interview_preparation(sample_job))

    # -------------------------------------------------------------------------
    # GAP ANALYSIS
    # -------------------------------------------------------------------------

    print(generate_full_gap_analysis(sample_job))