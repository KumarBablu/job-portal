"""
Ultra Robust Glassdoor Scraper
--------------------------------
Strategies:
1. Static HTML
2. Selenium Browser Fallback (BEST)

Works locally on Windows.
"""

import time
import random
import hashlib
import re
import requests

from bs4 import BeautifulSoup
from datetime import datetime
from urllib.parse import quote_plus

# Selenium
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By


# =========================================================
# USER AGENTS
# =========================================================

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
]


def _ua():
    return random.choice(USER_AGENTS)


# =========================================================
# HEADERS
# =========================================================

def _headers(referer="https://www.glassdoor.co.in/"):
    return {
        "User-Agent": _ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer,
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


# =========================================================
# HELPERS
# =========================================================

def make_job_id(url):
    return hashlib.md5(url.encode()).hexdigest()[:16]


def _make_session():
    s = requests.Session()

    try:
        s.get(
            "https://www.glassdoor.co.in/",
            headers=_headers(),
            timeout=15
        )

        time.sleep(random.uniform(1, 2))

    except Exception:
        pass

    return s


def scrape_glassdoor_job_details(job_url, session=None):
    details = {
        "Job Description": "",
        "Key Skills": "",
        "Job Highlights": "",
        "Role": "",
        "Industry Type": "",
        "Department": "",
        "Employment Type": "",
        "Education": "",
    }

    try:
        import json as _json

        if not session:
            session = _make_session()

        r = session.get(job_url, headers=_headers(job_url), timeout=20)
        if r.status_code != 200:
            return details

        soup = BeautifulSoup(r.text, "lxml")

        # JSON-LD first
        try:
            for s in soup.select("script[type='application/ld+json']"):
                try:
                    jd = _json.loads(s.string or s.text)
                except Exception:
                    continue
                if isinstance(jd, dict):
                    desc = jd.get("description")
                    if desc and not details["Job Description"]:
                        details["Job Description"] = BeautifulSoup(desc, "lxml").get_text(" ", strip=True)
                    skills = jd.get("skills") or jd.get("skillsRequired")
                    if skills and not details["Key Skills"]:
                        if isinstance(skills, list):
                            details["Key Skills"] = ", ".join(skills)[:800]
                        else:
                            details["Key Skills"] = str(skills)[:800]
        except Exception:
            pass

        # Meta description fallback
        try:
            meta_desc = soup.select_one("meta[property='og:description']") or soup.select_one("meta[name='description']")
            if meta_desc and not details["Job Description"]:
                details["Job Description"] = (meta_desc.get("content") or meta_desc.get("value") or "").strip()
        except Exception:
            pass

        # HTML selectors fallback
        if not details["Job Description"]:
            desc = soup.select_one("div.jobDescriptionContent") or soup.select_one("#JobDescriptionContainer") or soup.select_one("div.description") or soup.select_one(".jobDescription")
            if desc:
                details["Job Description"] = desc.get_text(" ", strip=True)

        bullets = [
            li.get_text(" ", strip=True)
            for li in soup.select("div.jobDescriptionContent li, #JobDescriptionContainer li, div.description li, .jobDescription li")
            if li.get_text(strip=True)
        ]
        if bullets and not details["Key Skills"]:
            details["Key Skills"] = ", ".join(dict.fromkeys(bullets))[:800]
        if bullets:
            details["Job Highlights"] = " | ".join(bullets[:6])

        # Heuristic regex lookups
        body = soup.get_text(" ", strip=True)
        patterns = {
            "Employment Type": r"(?:Employment Type|Job Type)[:\s]+([A-Za-z0-9, &-/]+)",
            "Education": r"(Bachelor(?:'s)?|Master(?:'s)?|MBA|B\.Tech|BE|BSc|MSc|Diploma)[^\n]{0,80}",
            "Industry Type": r"Industry[:\s]+([A-Za-z0-9, &-/]+)",
            "Department": r"Department[:\s]+([A-Za-z0-9, &-/]+)",
            "Role": r"(?:Role|Seniority level|Seniority)[:\s]+([A-Za-z0-9, &-/]+)",
        }
        for key, pattern in patterns.items():
            if details.get(key):
                continue
            match = re.search(pattern, body, re.I)
            if match:
                details[key] = match.group(1).strip()

        # Script embedded description fallback
        try:
            if not details["Job Description"]:
                for s in soup.select("script"):
                    txt = s.string or s.text
                    if not txt:
                        continue
                    m = re.search(r'"description"\s*:\s*"(.*?)"', txt, re.S)
                    if m:
                        val = m.group(1)
                        try:
                            val = bytes(val, "utf-8").decode("unicode_escape")
                        except Exception:
                            pass
                        details["Job Description"] = re.sub(r"\\n|\\r", " ", val).strip()
                        break
        except Exception:
            pass

    except Exception:
        pass

    return details


def _format_job(
    keyword,
    title,
    company,
    location,
    salary,
    experience,
    skills,
    url,
    extra_details=None
):
    if not extra_details:
        extra_details = {}

    return {
        "Job ID": make_job_id(url),
        "Platform": "Glassdoor",
        "Keyword": keyword,
        "Title": title,
        "Company": company,
        "Location": location,
        "Salary": salary,
        "Experience": experience,
        "Job Description": extra_details.get("Job Description") or skills,
        "Key Skills": extra_details.get("Key Skills") or skills,
        "Job Highlights": extra_details.get("Job Highlights") or "",
        "Role": extra_details.get("Role") or title,
        "Industry Type": extra_details.get("Industry Type") or "",
        "Department": extra_details.get("Department") or "",
        "Employment Type": extra_details.get("Employment Type") or "",
        "Education": extra_details.get("Education") or "",
        "Listing": f"{title} {company} {location} {skills}",
        "URL": url,
        "Scraped At": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "Match Score": 0,
        "AI Score": 0,
        "AI Reason": "",
        "Status": "New",
    }


# =========================================================
# MAIN ENTRY
# =========================================================

def scrape_glassdoor(keyword, location, max_jobs=25, log_fn=None):

    def log(msg):
        if log_fn:
            log_fn(msg)

    log(f"🔍 Glassdoor: {keyword} in {location}")

    # Strategy 1
    jobs = _try_html(keyword, location, max_jobs, log)

    if jobs:
        return jobs

    # Strategy 2
    jobs = _try_selenium(keyword, location, max_jobs, log)

    if jobs:
        return jobs

    log("  ⚠ Glassdoor returned 0 results")

    return []


# =========================================================
# STRATEGY 1 — HTML
# =========================================================

def _try_html(keyword, location, max_jobs, log):

    jobs = []

    try:

        session = _make_session()

        url = (
            "https://www.glassdoor.co.in/Job/jobs.htm?"
            f"sc.keyword={quote_plus(keyword)}"
            f"&locT=C&locId=&locKeyword={quote_plus(location)}"
        )

        r = session.get(
            url,
            headers=_headers(),
            timeout=20
        )

        log(f"  HTML status: {r.status_code}")

        if r.status_code != 200:
            return []

        soup = BeautifulSoup(r.text, "lxml")

        cards = soup.select("li.JobsList_jobListItem__wjTHv")

        log(f"  HTML cards: {len(cards)}")

        for card in cards[:max_jobs]:

            try:

                title_el = (
                    card.select_one("a.JobCard_jobTitle__GLyJ1")
                    or card.select_one("a")
                )

                if not title_el:
                    continue

                title = title_el.get_text(strip=True)

                job_url = title_el.get("href", "")

                if job_url.startswith("/"):
                    job_url = "https://www.glassdoor.co.in" + job_url

                company_el = card.select_one("span.EmployerProfile_compactEmployerName__9MGcV")

                company = (
                    company_el.get_text(strip=True)
                    if company_el else "Unknown"
                )

                loc_el = card.select_one("div.JobCard_location__Ds1fM")

                loc = (
                    loc_el.get_text(strip=True)
                    if loc_el else location
                )

                salary_el = card.select_one("div.JobCard_salaryEstimate__QpbTW")

                salary = (
                    salary_el.get_text(strip=True)
                    if salary_el else ""
                )

                extra = scrape_glassdoor_job_details(job_url, session)
                jobs.append(
                    _format_job(
                        keyword,
                        title,
                        company,
                        loc,
                        salary,
                        "",
                        "",
                        job_url,
                        extra,
                    )
                )

            except Exception:
                continue

    except Exception as e:
        log(f"  HTML error: {e}")

    return jobs


# =========================================================
# STRATEGY 2 — SELENIUM
# =========================================================

def _try_selenium(keyword, location, max_jobs, log):

    jobs = []

    driver = None

    try:

        from app.automation.browser import get_chrome_driver, is_chrome_available
        if not is_chrome_available():
            log("  ⚠ Browser fallback skipped: Google Chrome is not installed on this cloud server.")
            return []

        log("  Launching browser fallback...")
        import sys
        options = Options()
        options.add_argument("--disable-blink-features=AutomationControlled")
        if sys.platform != "win32" or os.environ.get("HEADLESS") == "1":
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--window-size=1920,1080")
        else:
            options.add_argument("--start-maximized")
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-popup-blocking")

        try:
            driver = get_chrome_driver(options)
        except Exception as e:
            log(f"  ⚠ Browser fallback unavailable: {e}")
            return []

        url = (
            "https://www.glassdoor.co.in/Job/jobs.htm?"
            f"sc.keyword={quote_plus(keyword)}"
            f"&locT=C&locId=&locKeyword={quote_plus(location)}"
        )

        driver.get(url)

        time.sleep(8)

        cards = driver.find_elements(
            By.CSS_SELECTOR,
            "li.JobsList_jobListItem__wjTHv"
        )

        log(f"  Selenium cards: {len(cards)}")

        for card in cards[:max_jobs]:

            try:

                title_el = None
                for selector in [
                    "a.JobCard_jobTitle__GLyJ1",
                    "a.jobLink",
                    "a",
                ]:
                    try:
                        title_el = card.find_element(By.CSS_SELECTOR, selector)
                        if title_el and title_el.get_attribute("href"):
                            break
                    except Exception:
                        title_el = None

                if not title_el:
                    continue

                title = title_el.text.strip() or title_el.get_attribute("innerText") or ""
                job_url = title_el.get_attribute("href") or ""
                if job_url.startswith("/"):
                    job_url = "https://www.glassdoor.co.in" + job_url
                if not job_url:
                    continue

                try:
                    company = card.find_element(
                        By.CSS_SELECTOR,
                        "span.EmployerProfile_compactEmployerName__9MGcV"
                    ).text.strip()
                except Exception:
                    company = "Unknown"

                try:
                    loc = card.find_element(
                        By.CSS_SELECTOR,
                        "div.JobCard_location__Ds1fM"
                    ).text.strip()
                except Exception:
                    loc = location

                try:
                    salary = card.find_element(
                        By.CSS_SELECTOR,
                        "div.JobCard_salaryEstimate__QpbTW"
                    ).text.strip()
                except Exception:
                    salary = ""

                extra = scrape_glassdoor_job_details(job_url)
                jobs.append(
                    _format_job(
                        keyword,
                        title,
                        company,
                        loc,
                        salary,
                        "",
                        "",
                        job_url,
                        extra,
                    )
                )

            except Exception:
                continue

    except Exception as e:
        log(f"  Selenium error: {e}")

    finally:

        try:
            if driver:
                driver.quit()
        except Exception:
            pass

    return jobs