"""
Open Jobs scraper — queries free, open public job APIs (Jobicy, Arbeitnow).
100% headless, requires no Selenium, Chrome, or API keys.
Works reliably on cloud hosting (Render, Railway, Fly, Heroku) and mobile scans.
"""
import hashlib
import html
import random
import re
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import quote_plus


def _ua():
    return random.choice([
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    ])


def make_job_id(url):
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:16]


def _clean_html(raw_html):
    if not raw_html:
        return ""
    try:
        soup = BeautifulSoup(raw_html, "html.parser")
        return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
    except Exception:
        clean = re.sub(r"<[^>]+>", " ", raw_html)
        return html.unescape(re.sub(r"\s+", " ", clean).strip())


def scrape_jobicy(keyword="", location="", max_jobs=25, log_fn=None):
    """
    Fetch remote/tech jobs from Jobicy API.
    Endpoint: https://jobicy.com/api/v2/remote-jobs
    """
    log = log_fn or (lambda m: None)
    jobs = []
    try:
        tag = quote_plus(keyword.strip().split()[0]) if keyword else ""
        url = f"https://jobicy.com/api/v2/remote-jobs?count={min(max(max_jobs * 2, 20), 50)}"
        if tag:
            url += f"&tag={tag}"

        log(f"  Fetching Jobicy: {url}")
        r = requests.get(url, headers={"User-Agent": _ua(), "Accept": "application/json"}, timeout=15)
        if r.status_code == 200:
            data = r.json()
            items = data.get("jobs", [])
            kw_terms = [t.lower() for t in (keyword or "").split() if len(t) > 2]

            for item in items:
                title = item.get("jobTitle", "").strip()
                company = item.get("companyName", "").strip() or "Tech Company"
                job_url = item.get("url", "").strip()
                if not title or not job_url:
                    continue

                desc_html = item.get("jobDescription", "") or item.get("jobExcerpt", "")
                desc = _clean_html(desc_html)
                loc = item.get("jobGeo", "").strip() or "Remote"

                # If keyword specified, do a loose relevance filter
                if kw_terms:
                    text_blob = f"{title} {desc}".lower()
                    if not any(term in text_blob for term in kw_terms):
                        continue

                salary = ""
                s_min = item.get("annualSalaryMin")
                s_max = item.get("annualSalaryMax")
                curr = item.get("salaryCurrency", "$")
                if s_min and s_max:
                    salary = f"{curr}{s_min:,} - {curr}{s_max:,} / yr"
                elif s_min:
                    salary = f"From {curr}{s_min:,} / yr"

                jobs.append({
                    "Job ID": make_job_id(job_url),
                    "Platform": "OpenJobs (Jobicy)",
                    "Keyword": keyword,
                    "Title": title,
                    "Company": company,
                    "Location": loc,
                    "Salary": salary,
                    "Experience": item.get("jobLevel", ""),
                    "Listing": f"{title} {company} {loc}",
                    "URL": job_url,
                    "Scraped At": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "Match Score": 0,
                    "AI Score": 0,
                    "AI Reason": "",
                    "Status": "New",
                    "Job Description": desc,
                    "Key Skills": ", ".join(item.get("jobIndustry", []) if isinstance(item.get("jobIndustry"), list) else [item.get("jobIndustry", "")]),
                    "Job Highlights": item.get("jobExcerpt", ""),
                    "Role": title,
                    "Industry Type": "Software / Technology",
                    "Department": item.get("jobCategory", ""),
                    "Employment Type": item.get("jobType", "Full-time"),
                    "Education": "",
                })
                if len(jobs) >= max_jobs:
                    break
    except Exception as e:
        log(f"  Jobicy fetch error: {e}")

    log(f"  [Jobicy] Returned {len(jobs)} jobs")
    return jobs


def scrape_arbeitnow(keyword="", location="", max_jobs=25, log_fn=None):
    """
    Fetch jobs from Arbeitnow public board API.
    Endpoint: https://www.arbeitnow.com/api/job-board-api
    """
    log = log_fn or (lambda m: None)
    jobs = []
    try:
        url = "https://www.arbeitnow.com/api/job-board-api"
        log(f"  Fetching Arbeitnow: {url}")
        r = requests.get(url, headers={"User-Agent": _ua(), "Accept": "application/json"}, timeout=15)
        if r.status_code == 200:
            data = r.json()
            items = data.get("data", [])
            kw_terms = [t.lower() for t in (keyword or "").split() if len(t) > 2]
            loc_term = (location or "").strip().lower()

            for item in items:
                title = item.get("title", "").strip()
                company = item.get("company_name", "").strip() or "Tech Company"
                job_url = item.get("url", "").strip()
                if not title or not job_url:
                    continue

                desc = _clean_html(item.get("description", ""))
                loc = item.get("location", "").strip() or ("Remote" if item.get("remote") else "Worldwide")
                tags = item.get("tags", [])
                skills_str = ", ".join(tags) if isinstance(tags, list) else str(tags)

                if kw_terms:
                    text_blob = f"{title} {skills_str} {desc}".lower()
                    if not any(term in text_blob for term in kw_terms):
                        continue

                if loc_term and loc_term not in ["remote", "any", "worldwide"]:
                    if loc_term not in loc.lower() and not item.get("remote"):
                        # If looking for a specific city and this job is neither in that city nor remote, skip
                        pass

                jobs.append({
                    "Job ID": make_job_id(job_url),
                    "Platform": "OpenJobs (Arbeitnow)",
                    "Keyword": keyword,
                    "Title": title,
                    "Company": company,
                    "Location": loc,
                    "Salary": "",
                    "Experience": "",
                    "Listing": f"{title} {company} {loc}",
                    "URL": job_url,
                    "Scraped At": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "Match Score": 0,
                    "AI Score": 0,
                    "AI Reason": "",
                    "Status": "New",
                    "Job Description": desc,
                    "Key Skills": skills_str,
                    "Job Highlights": "",
                    "Role": title,
                    "Industry Type": "Information Technology",
                    "Department": "",
                    "Employment Type": ", ".join(item.get("job_types", [])) if isinstance(item.get("job_types"), list) else "Full-time",
                    "Education": "",
                })
                if len(jobs) >= max_jobs:
                    break
    except Exception as e:
        log(f"  Arbeitnow fetch error: {e}")

    log(f"  [Arbeitnow] Returned {len(jobs)} jobs")
    return jobs


def scrape_open_jobs(keyword="", location="", max_jobs=25, log_fn=None):
    """
    Combined open jobs scraper querying Jobicy and Arbeitnow.
    Completely cloud and mobile friendly.
    """
    log = log_fn or (lambda m: None)
    log(f"[OpenJobs] Querying Open Job Feeds (Jobicy & Arbeitnow) for '{keyword}'...")

    results = []
    # Query Jobicy first
    half = max(max_jobs // 2, 10)
    jobicy_jobs = scrape_jobicy(keyword, location, max_jobs=half, log_fn=log)
    results.extend(jobicy_jobs)

    # Query Arbeitnow for remainder
    remaining = max(max_jobs - len(results), 10)
    arbeit_jobs = scrape_arbeitnow(keyword, location, max_jobs=remaining, log_fn=log)
    results.extend(arbeit_jobs)

    # Deduplicate by Job ID
    seen = set()
    unique_jobs = []
    for j in results:
        jid = j.get("Job ID")
        if jid not in seen:
            seen.add(jid)
            unique_jobs.append(j)

    log(f"[OpenJobs] Total returned: {len(unique_jobs)} jobs")
    return unique_jobs[:max_jobs]
