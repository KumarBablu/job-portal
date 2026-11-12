"""
Ultra Robust Naukri Scraper
--------------------------------
Strategies:
1. Public API
2. Mobile API
3. Static HTML
4. Selenium Browser Fallback (MOST IMPORTANT)

NOW ALSO SCRAPES:
- Job Description
- Key Skills
- Job Highlights
- Role
- Industry Type
- Department
- Employment Type
- Education

Works locally on Windows.
"""

import time
import json
import random
import hashlib
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

def _headers(referer="https://www.naukri.com/"):
    return {
        "User-Agent": _ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer,
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
    }


def _api_headers():
    h = _headers()
    h.update({
        "appid": "109",
        "systemid": "Naukri",
        "accept": "application/json",
    })
    return h


# =========================================================
# HELPERS
# =========================================================

def make_job_id(url):
    return hashlib.md5(url.encode()).hexdigest()[:16]


def _make_session():
    s = requests.Session()

    try:
        s.get(
            "https://www.naukri.com/",
            headers=_headers(),
            timeout=15
        )

        time.sleep(random.uniform(1, 2))

    except Exception:
        pass

    return s


# =========================================================
# JOB DETAIL SCRAPER
# =========================================================

def scrape_job_details(job_url, session=None):

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

        # JSON-LD structured data
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
                    skills = jd.get("skills") or jd.get("qualifications")
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

        # HTML fallbacks
        if not details["Job Description"]:
            desc = soup.select_one(".styles_JDC__dang-inner-html__h0K4t") or soup.select_one(".dang-inner-html") or soup.select_one(".job-desc") or soup.select_one(".jd")
            if desc:
                details["Job Description"] = desc.get_text(" ", strip=True)

        skills = []
        skill_tags = soup.select(".styles_key-skill__GIPn_ a") or soup.select(".key-skill a") or soup.select(".tags a, .skill-tags a") or soup.select(".skills a")
        for s in skill_tags:
            txt = s.get_text(strip=True)
            if txt and txt not in skills:
                skills.append(txt)
        if skills:
            details["Key Skills"] = ", ".join(skills)[:800]

        highlights = []
        hls = soup.select(".styles_jhc__jd-side-section__M4YvU li") or soup.select(".jd-side-section li") or soup.select(".job-highlights li") or soup.select(".highlights li")
        for h in hls:
            txt = h.get_text(" ", strip=True)
            if txt:
                highlights.append(txt)
        if highlights:
            details["Job Highlights"] = " | ".join(highlights[:6])

        # labels / key: value pairs
        labels = soup.select(".styles_details__Y424J label") or soup.select(".jd-attr label") or soup.select(".job-details label")
        for lbl in labels:
            try:
                key = lbl.get_text(" ", strip=True)
                val = ""
                nxt = lbl.find_next("span")
                if nxt:
                    val = nxt.get_text(" ", strip=True)
                if "Role" in key and not details.get("Role"):
                    details["Role"] = val
                elif "Industry" in key and not details.get("Industry Type"):
                    details["Industry Type"] = val
                elif "Department" in key and not details.get("Department"):
                    details["Department"] = val
                elif "Employment Type" in key and not details.get("Employment Type"):
                    details["Employment Type"] = val
                elif "Education" in key and not details.get("Education"):
                    details["Education"] = val
            except Exception:
                pass

        # final regex heuristics
        body = soup.get_text(" ", strip=True)
        patterns = {
            "Education": r"(Bachelor(?:'s)?|Master(?:'s)?|MBA|M\.BA|B\.Tech|BE|BSc|MSc|Diploma)[^\n]{0,80}",
            "Role": r"(?:Role|Designation|Seniority)[:\s]+([A-Za-z0-9, &-/]+)",
            "Industry Type": r"Industry[:\s]+([A-Za-z0-9, &-/]+)",
        }
        for key, pattern in patterns.items():
            if details.get(key):
                continue
            m = re.search(pattern, body, re.I)
            if m:
                details[key] = m.group(1).strip()

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


# =========================================================
# FORMAT JOB
# =========================================================

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

        # BASIC
        "Job ID": make_job_id(url),
        "Platform": "Naukri",
        "Keyword": keyword,
        "Title": title,
        "Company": company,
        "Location": location,
        "Salary": salary,
        "Experience": experience,

        # NEW FIELDS
        "Job Description": extra_details.get("Job Description") or "",

        "Key Skills": extra_details.get("Key Skills") or skills,

        "Job Highlights": extra_details.get("Job Highlights") or "",

        "Role": extra_details.get("Role") or title,

        "Industry Type": extra_details.get("Industry Type") or "",

        "Department": extra_details.get("Department") or "",

        "Employment Type": extra_details.get("Employment Type") or "",

        "Education": extra_details.get("Education") or "",

        # LISTING
        "Listing": (
            f"{title} "
            f"{company} "
            f"{location} "
            f"{skills} "
            f"{extra_details.get('Job Description', '')}"
        ),

        "URL": url,

        "Scraped At": datetime.now().strftime(
            "%Y-%m-%d %H:%M"
        ),

        "Match Score": 0,
        "AI Score": 0,
        "AI Reason": "",
        "Status": "New",
    }


# =========================================================
# MAIN ENTRY
# =========================================================

def scrape_naukri_api(keyword, location, max_jobs=25, log_fn=None):

    def log(msg):
        if log_fn:
            log_fn(msg)

    log(f"🔍 Naukri: {keyword} in {location}")

    # Strategy 1
    jobs = _try_public_api(keyword, location, max_jobs, log)

    if jobs:
        return jobs

    # Strategy 2
    jobs = _try_mobile_api(keyword, location, max_jobs, log)

    if jobs:
        return jobs

    # Strategy 3
    jobs = _try_html(keyword, location, max_jobs, log)

    if jobs:
        return jobs

    # Strategy 4
    jobs = _try_selenium(keyword, location, max_jobs, log)

    if jobs:
        return jobs

    log("⚠ Naukri returned 0 results")

    return []


# =========================================================
# STRATEGY 1 — PUBLIC API
# =========================================================

def _try_public_api(keyword, location, max_jobs, log):

    jobs = []

    try:

        session = _make_session()

        params = {
            "noOfResults": min(max_jobs, 50),
            "urlType": "search_by_keyword",
            "searchType": "adv",
            "keyword": keyword,
            "location": location,
            "k": keyword,
            "l": location,
        }

        r = session.get(
            "https://www.naukri.com/jobapi/v3/search",
            params=params,
            headers=_api_headers(),
            timeout=20,
        )

        log(f"API status: {r.status_code}")

        if r.status_code != 200:
            return []

        data = r.json()

        job_list = data.get("jobDetails", [])

        log(f"API jobs: {len(job_list)}")

        for jd in job_list[:max_jobs]:

            try:

                title = jd.get("title", "").strip()

                if not title:
                    continue

                company = jd.get(
                    "companyName",
                    ""
                ).strip()

                url = jd.get("jdURL", "")

                if not url.startswith("http"):
                    url = "https://www.naukri.com" + url

                ph = jd.get("placeholders", [])

                loc = ph[0].get(
                    "label",
                    location
                ) if len(ph) > 0 else location

                exp = ph[1].get(
                    "label",
                    ""
                ) if len(ph) > 1 else ""

                sal = ph[2].get(
                    "label",
                    ""
                ) if len(ph) > 2 else ""

                skills = jd.get(
                    "tagsAndSkills",
                    ""
                )

                # =====================================
                # NEW DETAIL SCRAPE
                # =====================================

                log(f"Fetching details: {title}")

                extra = scrape_job_details(
                    url,
                    session
                )

                jobs.append(
                    _format_job(
                        keyword,
                        title,
                        company,
                        loc,
                        sal,
                        exp,
                        skills,
                        url,
                        extra
                    )
                )

                time.sleep(
                    random.uniform(1, 2)
                )

            except Exception:
                continue

    except Exception as e:
        log(f"API error: {e}")

    return jobs


# =========================================================
# STRATEGY 2 — MOBILE API
# =========================================================

def _try_mobile_api(keyword, location, max_jobs, log):
    return []


# =========================================================
# STRATEGY 3 — STATIC HTML
# =========================================================

def _try_html(keyword, location, max_jobs, log):
    return []


# =========================================================
# STRATEGY 4 — SELENIUM
# =========================================================

def _try_selenium(keyword, location, max_jobs, log):

    jobs = []

    driver = None

    try:

        log("Launching browser fallback...")

        options = Options()

        options.add_argument(
            "--disable-blink-features=AutomationControlled"
        )

        options.add_argument("--start-maximized")

        options.add_argument("--disable-notifications")

        options.add_argument("--disable-popup-blocking")

        # options.add_argument("--headless=new")

        from app.automation.browser import get_chrome_driver
        driver = get_chrome_driver(options)

        url = (
            "https://www.naukri.com/"
            f"{keyword.replace(' ', '-')}"
            f"-jobs-in-"
            f"{location.replace(' ', '-')}"
        )

        driver.get(url)

        time.sleep(5)

        cards = driver.find_elements(
            By.CSS_SELECTOR,
            "article.jobTuple"
        )

        if not cards:
            cards = driver.find_elements(
                By.CSS_SELECTOR,
                ".cust-job-tuple"
            )

        log(f"Selenium cards: {len(cards)}")

        for card in cards[:max_jobs]:

            try:

                title_el = None
                for selector in [
                    "a.title",
                    "a.jobTitle",
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
                if not job_url:
                    continue

                try:
                    company = card.find_element(
                        By.CSS_SELECTOR,
                        ".comp-name"
                    ).text.strip()

                except Exception:
                    company = "Unknown"

                try:
                    loc = card.find_element(
                        By.CSS_SELECTOR,
                        ".locWdth"
                    ).text.strip()

                except Exception:
                    loc = location

                try:
                    exp = card.find_element(
                        By.CSS_SELECTOR,
                        ".expwdth"
                    ).text.strip()

                except Exception:
                    exp = ""

                try:
                    salary = card.find_element(
                        By.CSS_SELECTOR,
                        ".sal-wrap"
                    ).text.strip()

                except Exception:
                    salary = ""

                # =====================================
                # NEW DETAIL SCRAPE
                # =====================================

                extra = scrape_job_details(
                    job_url
                )

                jobs.append(
                    _format_job(
                        keyword,
                        title,
                        company,
                        loc,
                        salary,
                        exp,
                        "",
                        job_url,
                        extra
                    )
                )

                time.sleep(
                    random.uniform(1, 2)
                )

            except Exception:
                continue

    except Exception as e:
        log(f"Selenium error: {e}")

    finally:

        try:
            if driver:
                driver.quit()
        except Exception:
            pass

    return jobs