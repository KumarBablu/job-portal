"""
LinkedIn scraper — public search (no login) + browser session (with login).
Public search works fine on real machines; server IPs often get 403.
"""
import time, hashlib, requests, random, re
from bs4 import BeautifulSoup
from datetime import datetime
from urllib.parse import quote_plus


def _ua():
    return random.choice([
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    ])


def make_job_id(url):
    return hashlib.md5(url.encode()).hexdigest()[:16]


def scrape_linkedin_job_details(job_url, session=None):
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
        if not session:
            session = requests.Session()

        r = session.get(
            job_url,
            headers={
                "User-Agent": _ua(),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=20,
        )

        if r.status_code != 200:
            return details

        soup = BeautifulSoup(r.text, "lxml")

        desc = (
            soup.select_one("div.show-more-less-html__markup") or
            soup.select_one("div.description__text") or
            soup.select_one("div.jobs-description__content")
        )
        if desc:
            details["Job Description"] = desc.get_text(" ", strip=True)

        skills = []
        for sel in [
            "div.description__job-criteria-text",
            "span.job-criteria__text",
            "div.description__text li",
            "div.show-more-less-html__markup li",
            "ul.jobs-box__list li",
        ]:
            for tag in soup.select(sel):
                txt = tag.get_text(" ", strip=True)
                if txt and txt not in skills:
                    skills.append(txt)
        details["Key Skills"] = ", ".join(skills[:20])

        highlights = []
        for tag in soup.select("div.show-more-less-html__markup li, div.description__text li"):
            txt = tag.get_text(" ", strip=True)
            if txt:
                highlights.append(txt)
        details["Job Highlights"] = " | ".join(highlights[:6])

        for lbl in soup.select("span.jobs-unified-top-card__job-insight, span.jobs-unified-top-card__bullet, li"):
            txt = lbl.get_text(" ", strip=True)
            if not txt:
                continue
            if "Employment type" in txt or "Employment Type" in txt:
                details["Employment Type"] = txt
            elif "Seniority level" in txt or "Seniority" in txt:
                details["Role"] = txt
            elif "Industries" in txt or "Industry" in txt:
                details["Industry Type"] = txt
            elif "Department" in txt:
                details["Department"] = txt

        body = soup.get_text(" ", strip=True)
        if not details["Education"]:
            edu_match = re.search(r"(Bachelor(?:'s)?|Master(?:'s)?|MBA|M\.BA|B\.Tech|BE|BSc|MSc|Diploma)[^\n]{0,80}", body, re.I)
            if edu_match:
                details["Education"] = edu_match.group(0)

    except Exception:
        pass

    return details


def scrape_linkedin_public(keyword, location, max_jobs=25, log_fn=None, freshness_days=0):
    def log(m):
        if log_fn: log_fn(m)

    freshness_param = f"&f_TPR=r{freshness_days * 86400}" if freshness_days else ""
    log(f"🔍 LinkedIn: {keyword} in {location}")
    jobs = []

    # Try multiple page offsets for more results
    for start in range(0, min(max_jobs, 75), 25):
        url = (f"https://www.linkedin.com/jobs/search"
               f"?keywords={quote_plus(keyword)}"
               f"&location={quote_plus(location)}"
               f"{freshness_param}&start={start}")
        try:
            time.sleep(random.uniform(1, 2))
            r = requests.get(url, headers={
                "User-Agent": _ua(),
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }, timeout=20)

            if r.status_code != 200:
                log(f"  LinkedIn returned {r.status_code} (normal on server IPs)")
                break

            soup  = BeautifulSoup(r.text, "lxml")
            cards = (soup.select("div.base-card") or
                     soup.select("li.jobs-search-results__list-item") or
                     soup.select("[data-entity-urn*='jobPosting']"))

            log(f"  LinkedIn page offset={start}: {len(cards)} cards")
            if not cards:
                break

            for card in cards:
                try:
                    title_el = (card.select_one("h3.base-search-card__title") or
                                card.select_one("h3") or
                                card.select_one("a.base-card__full-link"))
                    title    = title_el.get_text(strip=True) if title_el else ""

                    url_el   = card.select_one("a.base-card__full-link") or card.select_one("a")
                    job_url  = url_el.get("href", "").split("?")[0] if url_el else ""

                    if not title or not job_url: continue

                    comp_el  = (card.select_one("h4.base-search-card__subtitle") or
                                card.select_one("a.hidden-nested-link") or card.select_one("h4"))
                    company  = comp_el.get_text(strip=True) if comp_el else "Unknown"

                    loc_el   = (card.select_one("span.job-search-card__location") or
                                card.select_one("[class*='location']"))
                    loc      = loc_el.get_text(strip=True) if loc_el else location

                    extra = scrape_linkedin_job_details(job_url)
                    jobs.append({
                        "Job ID": make_job_id(job_url), "Platform": "LinkedIn",
                        "Keyword": keyword, "Title": title, "Company": company,
                        "Location": loc, "Salary": "", "Experience": "",
                        "Listing": f"{title} {company} {loc}",
                        "URL": job_url, "Scraped At": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "Match Score": 0, "AI Score": 0, "AI Reason": "", "Status": "New",
                        **extra,
                    })
                    if len(jobs) >= max_jobs:
                        break
                except Exception:
                    continue

        except Exception as e:
            log(f"  LinkedIn error: {e}")
            break

        if len(jobs) >= max_jobs:
            break

    log(f"  ✅ LinkedIn: {len(jobs)} jobs for '{keyword}'")
    return jobs


def scrape_linkedin_with_browser(keyword, location, max_jobs=25, log_fn=None, freshness_days=0):
    """Use saved Playwright session (requires prior login_linkedin.py run)."""
    from app.automation.browser import start_browser

    def log(m):
        if log_fn: log_fn(m)

    freshness_param = f"&f_TPR=r{freshness_days * 86400}" if freshness_days else ""
    log(f"🌐 LinkedIn (browser): {keyword} in {location}")
    jobs = []

    playwright, browser, page = start_browser(headless=True)
    try:
        url = (f"https://www.linkedin.com/jobs/search/"
               f"?keywords={quote_plus(keyword)}"
               f"&location={quote_plus(location)}"
               f"{freshness_param}")
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(4)

        # Scroll to load more cards
        for _ in range(min(3, max_jobs // 10)):
            page.mouse.wheel(0, 3000)
            time.sleep(1.5)

        cards = page.locator("div.base-card, div.job-search-card").all()
        log(f"  Browser cards: {len(cards)}")

        for card in cards[:max_jobs]:
            try:
                card.click()
                time.sleep(2)

                title_el = page.locator("h1.t-24, h1.job-details-jobs-unified-top-card__job-title").first
                title    = title_el.inner_text(timeout=3000).strip() if title_el.count() else ""

                comp_el  = page.locator(".job-details-jobs-unified-top-card__company-name").first
                company  = comp_el.inner_text(timeout=3000).strip() if comp_el.count() else ""

                loc_el   = page.locator(".job-details-jobs-unified-top-card__bullet").first
                loc      = loc_el.inner_text(timeout=3000).strip() if loc_el.count() else location

                job_url  = page.url.split("?")[0]
                if not title or not job_url: continue

                extra = scrape_linkedin_job_details(job_url)
                jobs.append({
                    "Job ID": make_job_id(job_url), "Platform": "LinkedIn",
                    "Keyword": keyword, "Title": title, "Company": company,
                    "Location": loc, "Salary": "", "Experience": "",
                    "Listing": f"{title} {company} {loc}",
                    "URL": job_url, "Scraped At": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "Match Score": 0, "AI Score": 0, "AI Reason": "", "Status": "New",
                    **extra,
                })
            except Exception:
                continue
    finally:
        browser.close()
        playwright.stop()

    log(f"  ✅ LinkedIn browser: {len(jobs)} jobs")
    return jobs
