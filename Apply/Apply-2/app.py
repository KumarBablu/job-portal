"""
Multi-Platform Job Auto-Apply Bot
==================================
Supports: Naukri, LinkedIn, Indeed, Glassdoor + Company Site fallback

Architecture:
  - BasePlatform       : shared browser utils, safe_click, element finders
  - NaukriPlatform     : Naukri-specific selectors & chatbot loop
  - LinkedInPlatform   : LinkedIn Easy Apply multi-step form handler
  - IndeedPlatform     : Indeed resume-select + question handler
  - GlassdoorPlatform  : Glassdoor apply (Indeed-backed) handler
  - CompanySiteHandler : Generic best-effort form filler for company portals
  - BotOrchestrator    : reads jobs.xlsx, routes each job, saves progress
"""

import time
import os
import re
import json
import traceback
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse
from openpyxl import load_workbook, Workbook
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import (
    NoSuchElementException, TimeoutException,
    ElementClickInterceptedException, StaleElementReferenceException,
)

# ─── CONFIG ────────────────────────────────────────────────────────────────────
JOBS_FILE        = "jobs.xlsx"
OUTPUT_JOBS_FILE = "jobs_output.xlsx"
DETAILS_FILE     = "details.xlsx"
WAIT             = 12
STEP_DELAY       = 0.8
MAX_STEPS        = 20       # max chatbot/form steps per job
MAX_PAGES        = 8        # max LinkedIn Easy Apply pages

# "skip" (recommended, default): the moment a job redirects off-platform to an
#   external ATS or company career site, capture everything about the redirect,
#   mark the job "External Portal", and move on — no attempt to fill an
#   unfamiliar third-party form. This is far more reliable than trying to
#   drive every possible ATS blind.
# "attempt": keep the old behaviour of trying the best-effort generic/ATS
#   form-filler (CompanySiteHandler) on the redirected page before giving up.
EXTERNAL_PORTAL_MODE = os.environ.get("EXTERNAL_PORTAL_MODE", "skip").strip().lower()

# Structured, machine-readable event log (one JSON object per line) — used
# for diagnosing failures and building the "External Portals" queue in the
# dashboard. Human-readable prints continue to stream to stdout as before.
APPLY_EVENTS_LOG = Path(__file__).resolve().parent.parent.parent / "data" / "logs" / "apply_events.jsonl"
QUESTION_BANK_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "profile" / "question_bank.json"

# Known ATS providers, matched against the redirected domain/URL. Extend this
# as you encounter new portals — no code changes needed elsewhere, everything
# downstream (DB, dashboard, Excel export) just reads the resulting string.
ATS_DOMAIN_MAP = {
    "myworkdayjobs.com":      "Workday",
    "workday.com":            "Workday",
    "lever.co":               "Lever",
    "greenhouse.io":          "Greenhouse",
    "boards.greenhouse.io":   "Greenhouse",
    "successfactors.com":     "SuccessFactors",
    "successfactors.eu":      "SuccessFactors",
    "taleo.net":              "Taleo",
    "oraclecloud.com":        "Oracle Recruiting Cloud",
    "icims.com":              "iCIMS",
    "smartrecruiters.com":    "SmartRecruiters",
    "jobvite.com":            "Jobvite",
    "bamboohr.com":           "BambooHR",
    "jobdiva.com":            "JobDiva",
    "workable.com":           "Workable",
    "breezy.hr":              "Breezy HR",
    "recruitee.com":          "Recruitee",
    "ashbyhq.com":            "Ashby",
    "personio.com":           "Personio",
}


def detect_ats_from_url(url: str) -> str:
    """Best-effort ATS name from a redirect URL's domain. Returns 'Unknown'
    if it doesn't match a known provider (still perfectly fine to record —
    it just means we don't have a name for it yet)."""
    if not url:
        return "Unknown"
    host = urlparse(url).netloc.lower()
    for domain_key, name in ATS_DOMAIN_MAP.items():
        if domain_key in host:
            return name
    return "Unknown"


def log_event(**fields):
    """Append one structured JSON event to data/logs/apply_events.jsonl.
    Never raises — logging must not be able to crash the apply run."""
    try:
        APPLY_EVENTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        event = {"timestamp": datetime.now().isoformat(timespec="seconds"), **fields}
        with open(APPLY_EVENTS_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass


def normalize_question(question: str) -> str:
    return re.sub(r"\s+", " ", str(question or "").strip().lower())


def question_bank_answer(question: str) -> str:
    normalized = normalize_question(question)
    if not normalized or not QUESTION_BANK_FILE.exists():
        return ""
    try:
        entries = json.loads(QUESTION_BANK_FILE.read_text(encoding="utf-8"))
        for entry in entries if isinstance(entries, list) else []:
            if entry.get("normalized") == normalized:
                return str(entry.get("answer") or "").strip()
    except Exception:
        pass
    return ""


def remember_question(question: str, answer: str, source: str = "naukri"):
    normalized = normalize_question(question)
    if not normalized:
        return
    try:
        entries = []
        if QUESTION_BANK_FILE.exists():
            loaded = json.loads(QUESTION_BANK_FILE.read_text(encoding="utf-8"))
            entries = loaded if isinstance(loaded, list) else []
        now = datetime.now().isoformat(timespec="seconds")
        entry = next((item for item in entries if item.get("normalized") == normalized), None)
        if entry is None:
            entry = {"id": normalized, "question": question.strip(), "normalized": normalized,
                     "answer": str(answer or "").strip(), "source": source, "count": 0, "first_seen": now}
            entries.append(entry)
        elif answer and str(answer).strip():
            entry["answer"] = str(answer).strip()
        entry["count"] = int(entry.get("count") or 0) + 1
        entry["last_seen"] = now
        entry["source"] = source
        entries.sort(key=lambda item: item.get("last_seen", ""), reverse=True)
        QUESTION_BANK_FILE.parent.mkdir(parents=True, exist_ok=True)
        QUESTION_BANK_FILE.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def is_naukri_already_applied_button(text: str) -> bool:
    """Return whether a Naukri apply control represents an existing application."""
    return str(text or "").strip().lower().startswith("applied")


def has_naukri_applied_marker(text: str) -> bool:
    """Return whether page text contains Naukri's standalone Applied badge."""
    for line in str(text or "").splitlines():
        normalized = re.sub(r"\s+", " ", line).strip().lower()
        if re.fullmatch(r"applied(?: \d+ (?:day|days|hour|hours|month|months) ago)?", normalized):
            return True
    return False


def naukri_control_text(element) -> str:
    """Read visible and accessible text from a Naukri apply control."""
    values = [
        element.text,
        element.get_attribute("aria-label"),
        element.get_attribute("title"),
    ]
    return " ".join(str(value or "").strip() for value in values if value).strip().lower()


# ───────────────────────────────────────────────────────────────────────────────


# ══════════════════════════════════════════════════════════════════════════════
#  FIELD MAP  –  question keyword → details.xlsx column name
#  Add as many rows as needed.  More-specific keywords must come first.
# ══════════════════════════════════════════════════════════════════════════════
FIELD_MAP = [
    # ─── Compensation ───────────────────────────────────────────────────────
    (["current ctc", "current salary", "current package",
      "current compensation", "current annual"],             "current_ctc"),
    (["expected ctc", "expected salary", "expected package",
      "expected compensation", "desired salary",
      "salary expectation"],                                 "expected_ctc"),

    # ─── Experience ─────────────────────────────────────────────────────────
    (["notice period", "notice"],                            "notice_period"),
    (["relevant experience", "relevant exp"],                "relevant_experience"),
    (["total experience", "total exp", "years of exp",
      "how many years", "experience do you have",
      "years experience", "work experience",
      "overall experience"],                                 "total_experience"),

    # ─── Location ───────────────────────────────────────────────────────────
    (["current location", "current city", "where are you based",
      "where are you currently"],                            "current_location"),
    (["preferred location", "preferred city",
      "willing to relocate", "preferred"],                   "preferred_location"),

    # ─── Education ──────────────────────────────────────────────────────────
    (["qualification", "education", "degree",
      "highest qualification", "academic"],                  "highest_qualification"),

    # ─── Employer ───────────────────────────────────────────────────────────
    (["employer", "current company", "current organisation",
      "current organization", "company name",
      "where do you work"],                                  "current_employer"),

    # ─── Contact ────────────────────────────────────────────────────────────
    (["phone", "mobile", "contact number",
      "phone number"],                                       "phone"),
    (["email", "e-mail"],                                    "email"),

    # ─── Identity ───────────────────────────────────────────────────────────
    (["first name"],                                         "first_name"),
    (["last name", "surname", "family name"],                "last_name"),
    (["full name", "your name", "name"],                     "name"),

    # ─── Tech skills (order matters: more specific first) ───────────────────
    (["power bi", "powerbi"],                                "powerbi_experience"),
    (["machine learning", "ml pipeline",
      "pyspark", "spark"],                                   "relevant_experience"),
    (["python"],                                             "python_experience"),
    (["sql"],                                                "sql_experience"),
    (["excel"],                                              "excel_experience"),
    (["tableau"],                                            "tableau_experience"),
    (["r programming", " r "],                               "r_experience"),
    (["skill"],                                              "skills"),

    # ─── Catch-all ──────────────────────────────────────────────────────────
    (["experience", "exp"],                                  "total_experience"),
    (["visa", "work authoriz", "work permit",
      "eligible to work", "sponsorship"],                    "work_authorization"),
    (["gender"],                                             "gender"),
    (["veteran"],                                            "veteran_status"),
    (["disability", "disabled"],                             "disability_status"),
    (["ethnicity", "race", "hispanic"],                      "ethnicity"),
    (["linkedin", "linked in", "linkedin url",
      "profile url"],                                        "linkedin_url"),
    (["portfolio", "website", "personal url"],               "portfolio_url"),
    (["github"],                                             "github_url"),
    (["cover letter"],                                       "cover_letter"),
    (["city", "location"],                                   "current_location"),
    (["country"],                                            "country"),
    (["zip", "postal", "pincode"],                           "zip_code"),
]

# Yes/No questions — answered automatically
YES_KEYWORDS = [
    "are you legally", "authorized to work", "eligible to work",
    "18 years", "do you agree", "are you willing to",
    "do you have experience", "have you worked",
    "are you comfortable", "are you available",
]
NO_KEYWORDS = [
    "criminal", "convicted", "felony",
    "require visa sponsorship", "require sponsorship",
]

# Dropdown option preference lists (ordered best → worst)
NOTICE_PREFERENCES = ["immediate", "0", "15 days", "1 month", "30"]
LOCATION_YES       = ["yes", "open to relocation", "willing", "anywhere"]


# ══════════════════════════════════════════════════════════════════════════════
#  DETAILS LOADER
# ══════════════════════════════════════════════════════════════════════════════

def load_details(path: str = DETAILS_FILE) -> dict:
    wb = load_workbook(path)
    ws = wb.active
    headers = [str(c.value).strip().lower().replace(" ", "_")
               for c in ws[1] if c.value]
    row     = list(ws.iter_rows(min_row=2, max_row=2, values_only=True))[0]
    details = {}
    for h, v in zip(headers, row):
        details[h] = str(v).strip() if v else ""
    # Derived convenience fields
    if "name" in details and "first_name" not in details:
        parts = details["name"].split()
        details["first_name"] = parts[0] if parts else ""
        details["last_name"]  = " ".join(parts[1:]) if len(parts) > 1 else ""
    return details


# ══════════════════════════════════════════════════════════════════════════════
#  VALUE RESOLVER
# ══════════════════════════════════════════════════════════════════════════════

def get_value(question: str, details: dict) -> str:
    """Map a question string to the best answer from details."""
    q = question.lower()

    saved = question_bank_answer(question)
    if saved:
        return saved

    # Yes/No auto-answer
    if any(kw in q for kw in YES_KEYWORDS):
        return "Yes"
    if any(kw in q for kw in NO_KEYWORDS):
        return "No"

    for keywords, field in FIELD_MAP:
        if any(kw in q for kw in keywords):
            val = details.get(field, "").strip()
            if val:
                return val

    return ""


def best_option_index(option_texts: list, desired: str, question: str = "") -> int:
    """Choose the best index from a list of radio/dropdown options."""
    desired_l = desired.lower().strip()
    q         = question.lower()

    # Yes/No questions
    if any(kw in q for kw in YES_KEYWORDS):
        for i, opt in enumerate(option_texts):
            if opt.lower().strip() in ["yes", "true"]:
                return i
    if any(kw in q for kw in NO_KEYWORDS):
        for i, opt in enumerate(option_texts):
            if opt.lower().strip() in ["no", "false"]:
                return i

    if not desired_l:
        # Return first non-skip option
        for i, opt in enumerate(option_texts):
            if "skip" not in opt.lower() and "prefer not" not in opt.lower():
                return i
        return 0

    desired_nums = re.findall(r'\d+', desired_l)

    # Exact / substring match
    for i, opt in enumerate(option_texts):
        ol = opt.lower().strip()
        if ol == desired_l or desired_l in ol or ol in desired_l:
            return i

    # Numeric match
    if desired_nums:
        for i, opt in enumerate(option_texts):
            if re.findall(r'\d+', opt.lower()) == desired_nums:
                return i

    # First non-skip option
    for i, opt in enumerate(option_texts):
        if "skip" not in opt.lower() and "prefer not" not in opt.lower():
            return i
    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  BASE PLATFORM  –  shared utilities
# ══════════════════════════════════════════════════════════════════════════════

class BasePlatform:

    def __init__(self, driver: webdriver.Chrome, details: dict):
        self.driver  = driver
        self.details = details
        self.wait    = WebDriverWait(driver, WAIT)

    # ── Safe click ────────────────────────────────────────────────────────────
    def safe_click(self, element):
        self.driver.execute_script(
            "arguments[0].scrollIntoView({block:'center',inline:'center'});",
            element
        )
        time.sleep(0.35)
        try:
            element.click()
        except ElementClickInterceptedException:
            self.driver.execute_script("arguments[0].click();", element)

    # ── First visible CSS ─────────────────────────────────────────────────────
    def _first_visible_css(self, selectors: list):
        for sel in selectors:
            try:
                for el in self.driver.find_elements(By.CSS_SELECTOR, sel):
                    if el.is_displayed():
                        return el
            except Exception:
                pass
        return None

    # ── First visible XPath ───────────────────────────────────────────────────
    def _first_visible_xpath(self, xpaths: list):
        for xp in xpaths:
            try:
                for el in self.driver.find_elements(By.XPATH, xp):
                    if el.is_displayed():
                        return el
            except Exception:
                pass
        return None

    # ── Wait for page load ────────────────────────────────────────────────────
    def wait_page(self, timeout: int = 10):
        WebDriverWait(self.driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )

    # ── Fill text input ───────────────────────────────────────────────────────
    def fill_input(self, element, value: str):
        """Clear and fill any input (standard or contenteditable)."""
        try:
            ce = element.get_attribute("contenteditable") == "true"
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", element
            )
            time.sleep(0.3)
            self.safe_click(element)
            time.sleep(0.3)
            if ce:
                self.driver.execute_script(
                    "arguments[0].innerHTML='';arguments[0].textContent='';",
                    element
                )
            else:
                element.send_keys(Keys.CONTROL + "a")
                element.send_keys(Keys.DELETE)
                element.clear()
            time.sleep(0.2)
            element.send_keys(value)
            time.sleep(0.4)
        except Exception as e:
            print(f"    ⚠️  fill_input: {e}")

    # ── Handle <select> dropdown ──────────────────────────────────────────────
    def fill_select(self, element, desired: str, question: str = ""):
        try:
            sel = Select(element)
            options = [o.text.strip() for o in sel.options]
            idx = best_option_index(options, desired, question)
            sel.select_by_index(idx)
            print(f"    📋 Select: {options[idx]!r} (want: {desired!r})")
        except Exception as e:
            print(f"    ⚠️  fill_select: {e}")

    # ── Generic: fill one form field ──────────────────────────────────────────
    def fill_field(self, element, label: str):
        """Auto-fill any form element given its label text."""
        tag   = element.tag_name.lower()
        etype = (element.get_attribute("type") or "").lower()
        value = get_value(label, self.details)

        if tag == "select":
            self.fill_select(element, value, label)
            return

        if etype in ["radio", "checkbox"]:
            # handled externally
            return

        if tag in ["input", "textarea"] or \
           element.get_attribute("contenteditable") == "true":
            if not value:
                value = "3"  # safe fallback
            self.fill_input(element, value)
            print(f"    ✏️  {label!r} → {value!r}")
            return

    # ── Handle any visible form on page (generic) ─────────────────────────────
    def handle_generic_form_fields(self, container=None):
        """
        Walk all visible inputs inside `container` (default: body),
        match label → answer, and fill.
        Returns number of fields filled.
        """
        root   = container or self.driver.find_element(By.TAG_NAME, "body")
        filled = 0

        # ── Text / textarea inputs ────────────────────────────────────────────
        for inp in root.find_elements(
            By.CSS_SELECTOR, "input[type='text'], input[type='email'], "
                             "input[type='tel'], input[type='number'], "
                             "input:not([type]), textarea"
        ):
            if not inp.is_displayed() or not inp.is_enabled():
                continue
            if inp.get_attribute("readonly"):
                continue

            label = self._label_for(inp)
            if not label:
                continue

            val = get_value(label, self.details)
            if val:
                self.fill_input(inp, val)
                print(f"    ✏️  [{label}] → {val}")
                filled += 1

        # ── <select> dropdowns ────────────────────────────────────────────────
        for sel_el in root.find_elements(By.CSS_SELECTOR, "select"):
            if not sel_el.is_displayed():
                continue
            label = self._label_for(sel_el)
            if not label:
                continue
            val = get_value(label, self.details)
            self.fill_select(sel_el, val, label)
            filled += 1

        # ── Radio groups ──────────────────────────────────────────────────────
        radio_groups: dict = {}
        for radio in root.find_elements(By.CSS_SELECTOR, "input[type='radio']"):
            if not radio.is_displayed():
                continue
            name = radio.get_attribute("name") or ""
            if name:
                radio_groups.setdefault(name, []).append(radio)

        for name, radios in radio_groups.items():
            labels = []
            for r in radios:
                lbl = self._label_for(r) or (r.get_attribute("value") or "")
                labels.append(lbl)
            question = self._group_question(radios[0])
            desired  = get_value(question, self.details)
            idx      = best_option_index(labels, desired, question)
            try:
                self.safe_click(radios[idx])
                print(f"    📻 Radio [{question}] → {labels[idx]!r}")
                filled += 1
            except Exception:
                pass

        return filled

    def _label_for(self, element) -> str:
        """Return the best human-readable label for a form element."""
        # 1. aria-label
        v = element.get_attribute("aria-label") or ""
        if v.strip():
            return v.strip()
        # 2. placeholder
        v = element.get_attribute("placeholder") or ""
        if v.strip():
            return v.strip()
        # 3. <label for="id">
        eid = element.get_attribute("id") or ""
        if eid:
            try:
                lbl = self.driver.find_element(
                    By.CSS_SELECTOR, f"label[for='{eid}']"
                )
                if lbl.text.strip():
                    return lbl.text.strip()
            except Exception:
                pass
        # 4. aria-labelledby
        lby = element.get_attribute("aria-labelledby") or ""
        if lby:
            try:
                ref = self.driver.find_element(By.ID, lby)
                if ref.text.strip():
                    return ref.text.strip()
            except Exception:
                pass
        # 5. name attribute
        v = element.get_attribute("name") or ""
        if v.strip():
            return v.strip().replace("_", " ").replace("-", " ")
        return ""

    def _group_question(self, radio_element) -> str:
        """Try to find the fieldset legend or surrounding question for a radio."""
        try:
            fs = radio_element.find_element(
                By.XPATH, "ancestor::fieldset[1]"
            )
            leg = fs.find_element(By.TAG_NAME, "legend")
            if leg.text.strip():
                return leg.text.strip()
        except Exception:
            pass
        # Walk up the DOM looking for a label-like element
        try:
            parent = radio_element.find_element(
                By.XPATH,
                "ancestor::div[contains(@class,'question') or "
                "contains(@class,'field') or contains(@class,'form-group')][1]"
            )
            for tag in ["label", "legend", "h3", "h4", "p", "span"]:
                els = parent.find_elements(By.TAG_NAME, tag)
                for el in els:
                    txt = el.text.strip()
                    if len(txt) > 3 and "?" in txt or len(txt) > 10:
                        return txt
        except Exception:
            pass
        return self._label_for(radio_element)

    # ── Check for success/already-applied text ────────────────────────────────
    def body_text(self) -> str:
        try:
            return self.driver.find_element(By.TAG_NAME, "body").text.lower()
        except Exception:
            return ""

    def is_success(self) -> bool:
        body = self.body_text()
        return any(s in body for s in [
            "successfully applied", "application submitted",
            "thank you for applying", "application received",
            "application sent", "application complete",
            "your application has been", "applied successfully",
        ])

    def is_already_applied(self) -> bool:
        body = self.body_text()
        return any(s in body for s in [
            "already applied", "you have already applied",
            "application already submitted", "applied on",
            "you have applied", "applied successfully",
            "your application has been submitted",
            "application sent", "already submitted your application",
        ])

    # ── Upload resume ─────────────────────────────────────────────────────────
    def try_upload_resume(self, container=None):
        path = os.path.abspath(self.details.get("resume_path", ""))
        if not os.path.exists(path):
            return
        root = container or self.driver.find_element(By.TAG_NAME, "body")
        for fi in root.find_elements(By.CSS_SELECTOR, "input[type='file']"):
            try:
                fi.send_keys(path)
                print("    📎 Resume uploaded")
                time.sleep(2)
                return
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
#  NAUKRI PLATFORM
# ══════════════════════════════════════════════════════════════════════════════

class NaukriPlatform(BasePlatform):

    # ── Panel selectors ───────────────────────────────────────────────────────
    PANEL_CSS = [
        "div.ssrc__chatbot-container", "div[class*='ssrc__chatbot']",
        "div[class*='chatbot-container']", "div[class*='chatbot']",
        "div[class*='applyBot']", "div[class*='apply-bot']",
        "div[class*='apply-panel']",
    ]
    QUESTION_CSS = [
        "div.ssrc__bot-msg", "div[class*='bot-msg']",
        "div[class*='bot-message']", "div[class*='botMessage']",
        "div[class*='question-text']", "span[class*='question']",
    ]
    OPTION_CSS = [
        "label.ssrc__option", "label[class*='ssrc__option']",
        "label[class*='option']", "div[class*='option-row']",
        "div[class*='optionRow']", "div[class*='chatbot'] label",
        "div[class*='ssrc'] label",
    ]
    SAVE_XPATH = [
        "//div[normalize-space()='Save']",
        "//div[contains(@class,'sendMsg') and normalize-space()='Save']",
        "//button[normalize-space()='Save']", "//button[normalize-space()='Submit']",
        "//button[normalize-space()='Next']", "//button[normalize-space()='Continue']",
        "//button[contains(@class,'ssrc__save')]",
        "//button[contains(@class,'save-btn')]",
        "//button[contains(@class,'submit')]",
        "//div[contains(text(),'Save')]", "//div[contains(text(),'Next')]",
    ]

    def login(self):
        self.driver.get("https://www.naukri.com/nlogin/login")
        WebDriverWait(self.driver, WAIT).until(
            EC.presence_of_element_located((By.ID, "usernameField"))
        ).send_keys(self.details.get("email", ""))
        self.driver.find_element(By.ID, "passwordField").send_keys(
            self.details.get("password", "")
        )
        self.driver.find_element(
            By.XPATH, "//button[contains(text(),'Login')]"
        ).click()
        time.sleep(5)
        print("✅ Naukri: logged in")

    def find_panel(self):
        return self._first_visible_css(self.PANEL_CSS)

    def is_naukri_applied_page(self) -> bool:
        return has_naukri_applied_marker(self.body_text()) or self.is_already_applied()

    def find_question(self) -> str:
        for sel in self.QUESTION_CSS:
            try:
                els = [e for e in self.driver.find_elements(By.CSS_SELECTOR, sel)
                       if e.is_displayed() and e.text.strip()]
                if els:
                    return els[-1].text.strip()
            except Exception:
                pass
        panel = self.find_panel()
        if panel:
            skip = {"Save", "Next", "Submit", "Skip this question",
                    "Type message here...", "Continue"}
            lines = [l.strip() for l in panel.text.splitlines()
                     if l.strip() and l.strip() not in skip]
            if lines:
                return lines[-1]
        return ""

    def find_options(self) -> list:
        for sel in self.OPTION_CSS:
            try:
                els = self.driver.find_elements(By.CSS_SELECTOR, sel)
                visible = [(el, el.text.strip()) for el in els
                           if el.is_displayed() and el.text.strip()
                           and el.text.strip() not in
                           {"Save", "Next", "Submit", "Type message here..."}]
                if len(visible) >= 2:
                    return visible
            except Exception:
                pass
        return []

    def find_text_input(self):
        for sel in [
            "div[contenteditable='true']", "[contenteditable='true']",
            "textarea", "input[type='text']", "input:not([type])",
        ]:
            try:
                for el in self.driver.find_elements(By.CSS_SELECTOR, sel):
                    if not el.is_displayed() or not el.is_enabled():
                        continue
                    if el.tag_name.lower() in ["body", "html"]:
                        continue
                    cls = (el.get_attribute("class") or "").lower()
                    if "search" in cls:
                        continue
                    return el
            except Exception:
                pass
        return None

    def find_save_btn(self):
        for xp in self.SAVE_XPATH:
            try:
                for el in self.driver.find_elements(By.XPATH, xp):
                    if el.is_displayed():
                        txt = el.text.strip().lower()
                        if txt in ["save", "submit", "next", "continue"]:
                            return el
            except Exception:
                pass
        return None

    def click_apply(self):
        candidates = []
        try:
            el = self.driver.find_element(By.ID, "apply-button")
            if el.is_displayed():
                candidates.append(el)
        except Exception:
            pass
        for sel in [
            "button.apply-button", "button[class*='apply-button']",
            "a[class*='apply-button']", "button", "a", "[role='button']",
            "[class*='apply']", "[id*='apply']",
        ]:
            for el in self.driver.find_elements(By.CSS_SELECTOR, sel):
                if el.is_displayed() and el not in candidates:
                    candidates.append(el)
        for xp in [
            "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'apply')]",
            "//a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'apply')]",
            "//*[@role='button' and contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'apply')]",
        ]:
            for el in self.driver.find_elements(By.XPATH, xp):
                if el.is_displayed() and el not in candidates:
                    candidates.append(el)

        for el in candidates:
            try:
                txt = naukri_control_text(el)
                if "apply" not in txt:
                    continue
                if is_naukri_already_applied_button(txt):
                    return "already_applied"
                try:
                    if el.get_attribute("disabled") and "appl" in txt:
                        return "already_applied"
                except Exception:
                    pass
                if "apply on company site" in txt:
                    self.safe_click(el)
                    return "company_site_click"
                if "apply" in txt:
                    self.safe_click(el)
                    return "clicked"
            except Exception:
                pass
        return False

    def handle_panel(self) -> str:
        time.sleep(3)

        # New tab = company site
        if len(self.driver.window_handles) > 1:
            self.driver.switch_to.window(self.driver.window_handles[-1])
            url = self.driver.current_url
            self.driver.close()
            self.driver.switch_to.window(self.driver.window_handles[0])
            return url  # company site URL

        if "naukri.com" not in self.driver.current_url:
            return self.driver.current_url  # redirect

        for _ in range(12):
            if self.find_panel():
                break
            time.sleep(1)
        else:
            if self.is_already_applied():
                return "already_applied"
            return "applied"

        print("    📋 Chatbot panel open")
        prev_q       = ""
        stuck        = 0
        empty_streak = 0

        for step in range(1, MAX_STEPS + 1):
            time.sleep(STEP_DELAY)

            if self.is_success():
                return "applied"

            if not self.find_panel():
                return "applied"

            question = self.find_question()
            print(f"    ── Step {step} ❓ {question!r}")

            # Resume upload
            if any(kw in question.lower()
                   for kw in ["resume", "cv", "curriculum", "upload"]):
                self.try_upload_resume()

            if not question:
                empty_streak += 1
                if self.is_success():
                    return "applied"
                if empty_streak <= 4:
                    time.sleep(1.5)
                    continue
                if not self.find_panel():
                    return "applied"
                return "applied"

            if question == prev_q:
                stuck += 1
                if stuck >= 3:
                    btn = self.find_save_btn()
                    if btn:
                        self.safe_click(btn)
                    stuck = 0
                else:
                    time.sleep(1)
                continue
            else:
                stuck = 0
            prev_q = question

            options = self.find_options()
            if options:
                texts   = [t for _, t in options]
                desired = get_value(question, self.details)
                idx     = best_option_index(texts, desired, question)
                el, txt = options[idx]
                remember_question(question, txt, "naukri")
                print(f"    📻 Pick [{idx}] {txt!r}")
                self.safe_click(el)
                time.sleep(0.5)
                btn = self.find_save_btn()
                if btn:
                    self.safe_click(btn)
                continue

            inp = self.find_text_input()
            if inp:
                answer = get_value(question, self.details) or "3"
                remember_question(question, answer, "naukri")
                print(f"    ✏️  → {answer!r}")
                self.fill_input(inp, answer)
                time.sleep(1)
                btn = self.find_save_btn()
                if btn:
                    self.safe_click(btn)
                    time.sleep(2)
                continue

            btn = self.find_save_btn()
            if btn:
                self.safe_click(btn)
                continue

            time.sleep(2)

        return "applied"

    def apply(self, url: str) -> str:
        try:
            self.driver.get(url)
            self.wait_page()

            if self.is_naukri_applied_page():
                return "already_applied"
            try:
                btn_txt = self.driver.find_element(
                    By.ID, "apply-button"
                ).text.strip().lower()
                if btn_txt == "applied":
                    return "already_applied"
            except Exception:
                pass

            result = self.click_apply()
            if result == "already":
                return "already_applied"
            if not result:
                # No clickable Apply button found at all — before giving up,
                # re-check for an already-applied state one more time. This
                # catches jobs where the page renders differently once
                # already applied (no Apply button at all, rather than a
                # disabled/relabeled one), which the earlier checks miss.
                if self.is_naukri_applied_page():
                    return "already_applied"
                return "error"

            return self.handle_panel()

        except KeyboardInterrupt:
            raise
        except Exception as e:
            if _is_dead_session_error(e):
                # Don't swallow this — main()'s retry loop needs to see it
                # to restart the browser, instead of every remaining job
                # failing identically against a dead session.
                raise
            print(f"    ❌ Naukri error:\n{traceback.format_exc()}")
            return "error"


# ══════════════════════════════════════════════════════════════════════════════
#  LINKEDIN PLATFORM  –  Easy Apply
# ══════════════════════════════════════════════════════════════════════════════

class LinkedInPlatform(BasePlatform):

    def login(self):
        self.driver.get("https://www.linkedin.com/login")
        WebDriverWait(self.driver, WAIT).until(
            EC.presence_of_element_located((By.ID, "username"))
        ).send_keys(self.details.get("email", ""))
        self.driver.find_element(By.ID, "password").send_keys(
            self.details.get("password", "")
        )
        self.driver.find_element(
            By.CSS_SELECTOR, "button[type='submit']"
        ).click()
        time.sleep(5)
        print("✅ LinkedIn: logged in")

    def click_easy_apply(self) -> bool:
        for sel in [
            "button.jobs-apply-button",
            "button[class*='jobs-apply-button']",
            ".jobs-s-apply button",
        ]:
            try:
                el = WebDriverWait(self.driver, 8).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
                )
                txt = el.text.strip().lower()
                if "easy apply" in txt or "apply" in txt:
                    self.safe_click(el)
                    print("    🖱️  Easy Apply clicked")
                    return True
            except Exception:
                pass
        for xp in [
            "//button[contains(.,'Easy Apply')]",
            "//button[contains(.,'Apply')]",
        ]:
            try:
                el = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, xp))
                )
                self.safe_click(el)
                return True
            except Exception:
                pass
        return False

    def _get_modal(self):
        for sel in [
            "div[data-test-modal]",
            "div.jobs-easy-apply-modal",
            "div[class*='easy-apply']",
            "div[role='dialog']",
        ]:
            try:
                el = self.driver.find_element(By.CSS_SELECTOR, sel)
                if el.is_displayed():
                    return el
            except Exception:
                pass
        return None

    def _extract_label(self, element) -> str:
        """Get the question/label nearest to an input in the LinkedIn modal."""
        label = self._label_for(element)
        if label:
            return label
        # Walk up to find label/legend/h3
        try:
            parent = element.find_element(
                By.XPATH,
                "ancestor::div[contains(@class,'fb-dash-form-element') or "
                "contains(@class,'jobs-easy-apply-form-element') or "
                "contains(@class,'form-component')][1]"
            )
            for tag in ["label", "legend", "h3", "span[class*='label']"]:
                els = parent.find_elements(By.CSS_SELECTOR, tag)
                for el in els:
                    t = el.text.strip()
                    if len(t) > 2:
                        return t
        except Exception:
            pass
        return ""

    def _handle_page(self, modal) -> bool:
        """Fill one page of the Easy Apply modal. Return True if advanced."""
        time.sleep(1.5)

        # ── Resume/cover letter upload ────────────────────────────────────────
        self.try_upload_resume(modal)

        # ── Text inputs ───────────────────────────────────────────────────────
        for inp in modal.find_elements(
            By.CSS_SELECTOR,
            "input[type='text'], input[type='email'], input[type='tel'], "
            "input[type='number'], input:not([type]), textarea"
        ):
            if not inp.is_displayed() or not inp.is_enabled():
                continue
            if inp.get_attribute("readonly"):
                continue
            current_val = inp.get_attribute("value") or inp.text or ""
            if current_val.strip():
                continue   # already filled
            label = self._extract_label(inp)
            val   = get_value(label, self.details)
            if val:
                self.fill_input(inp, val)
                print(f"    ✏️  [{label}] → {val}")

        # ── Dropdowns ─────────────────────────────────────────────────────────
        for sel_el in modal.find_elements(By.CSS_SELECTOR, "select"):
            if not sel_el.is_displayed():
                continue
            label = self._extract_label(sel_el)
            val   = get_value(label, self.details)
            self.fill_select(sel_el, val, label)

        # ── Typeahead / combobox (LinkedIn uses these for location, skills) ───
        for combo in modal.find_elements(
            By.CSS_SELECTOR, "input[role='combobox'], input[aria-autocomplete]"
        ):
            if not combo.is_displayed():
                continue
            if combo.get_attribute("value"):
                continue
            label = self._extract_label(combo)
            val   = get_value(label, self.details)
            if val:
                self.fill_input(combo, val)
                time.sleep(1)
                # Select first dropdown suggestion
                for opt_sel in [
                    "div[role='option']", "li[role='option']",
                    "div.basic-typeahead__selectable"
                ]:
                    opts = self.driver.find_elements(By.CSS_SELECTOR, opt_sel)
                    visible = [o for o in opts if o.is_displayed()]
                    if visible:
                        self.safe_click(visible[0])
                        break

        # ── Radio groups ──────────────────────────────────────────────────────
        radio_groups: dict = {}
        for radio in modal.find_elements(By.CSS_SELECTOR, "input[type='radio']"):
            if not radio.is_displayed():
                continue
            name = radio.get_attribute("name") or radio.get_attribute("id") or ""
            radio_groups.setdefault(name, []).append(radio)

        for name, radios in radio_groups.items():
            # Find the question text
            question = ""
            for r in radios:
                question = self._extract_label(r)
                if question:
                    break

            # Collect option labels
            opt_labels = []
            for r in radios:
                lbl = self._label_for(r) or r.get_attribute("value") or ""
                opt_labels.append(lbl)

            desired = get_value(question, self.details)
            idx     = best_option_index(opt_labels, desired, question)
            try:
                self.safe_click(radios[idx])
                print(f"    📻 [{question}] → {opt_labels[idx]!r}")
            except Exception:
                pass

        # ── Checkboxes (terms, EEO, etc.) ─────────────────────────────────────
        for cb in modal.find_elements(By.CSS_SELECTOR, "input[type='checkbox']"):
            if not cb.is_displayed():
                continue
            label = self._label_for(cb).lower()
            # Only auto-tick if it looks like agreement/optional EEO disclosure
            if any(kw in label for kw in
                   ["agree", "terms", "eeoc", "voluntary", "authorize",
                    "certify", "confirm"]):
                if not cb.is_selected():
                    self.safe_click(cb)

        # ── Next / Review / Submit ────────────────────────────────────────────
        for btn_text in ["Submit application", "Review", "Next", "Continue"]:
            try:
                btn = modal.find_element(
                    By.XPATH,
                    f".//button[contains(normalize-space(.),'{btn_text}')]"
                )
                if btn.is_displayed() and btn.is_enabled():
                    time.sleep(0.8)
                    self.safe_click(btn)
                    print(f"    ➡️  Clicked: {btn_text}")
                    return True
            except Exception:
                pass
        return False

    def _is_complete(self, modal) -> bool:
        """Detect final submission success inside modal or page."""
        if self.is_success():
            return True
        try:
            modal.find_element(
                By.XPATH,
                ".//*[contains(text(),'Your application was sent') or "
                "contains(text(),'application submitted') or "
                "contains(text(),'Successfully applied')]"
            )
            return True
        except Exception:
            pass
        return False

    def _discard_modal(self):
        """Close any open modal (e.g. after already-applied detection)."""
        for sel in [
            "button[aria-label='Dismiss']", "button[data-test-modal-close-btn]",
            "button.artdeco-modal__dismiss",
        ]:
            try:
                el = self.driver.find_element(By.CSS_SELECTOR, sel)
                if el.is_displayed():
                    self.safe_click(el)
                    return
            except Exception:
                pass

    def apply(self, url: str) -> str:
        try:
            self.driver.get(url)
            self.wait_page()

            if self.is_already_applied():
                return "already_applied"

            # Already-applied badge (LinkedIn shows "Applied X days ago")
            try:
                self.driver.find_element(
                    By.XPATH,
                    "//*[contains(@class,'jobs-s-apply') and "
                    "contains(.,'Applied')]"
                )
                return "already_applied"
            except Exception:
                pass

            if not self.click_easy_apply():
                # Not Easy Apply → company site
                for xp in ["//button[contains(.,'Apply')]",
                            "//a[contains(.,'Apply')]"]:
                    try:
                        el = self.driver.find_element(By.XPATH, xp)
                        if el.is_displayed():
                            return el.get_attribute("href") or "company_site"
                    except Exception:
                        pass
                return "error"

            time.sleep(2)

            for page in range(1, MAX_PAGES + 1):
                modal = self._get_modal()
                if not modal:
                    # Modal closed — check success
                    return "applied" if self.is_success() else "error"

                if self._is_complete(modal):
                    return "applied"

                print(f"    📄 Easy Apply page {page}")
                self._handle_page(modal)
                time.sleep(2)

                if self._is_complete(modal) or self.is_success():
                    return "applied"

            return "applied"  # hit page limit

        except KeyboardInterrupt:
            raise
        except Exception as e:
            if _is_dead_session_error(e):
                # Don't swallow this — main()'s retry loop needs to see it
                # to restart the browser, instead of every remaining job
                # failing identically against a dead session.
                raise
            print(f"    ❌ LinkedIn error:\n{traceback.format_exc()}")
            return "error"


# ══════════════════════════════════════════════════════════════════════════════
#  INDEED PLATFORM
# ══════════════════════════════════════════════════════════════════════════════

class IndeedPlatform(BasePlatform):

    def login(self):
        self.driver.get("https://secure.indeed.com/auth")
        time.sleep(3)
        try:
            email_inp = WebDriverWait(self.driver, WAIT).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "input[type='email'], input[name='__email']")
                )
            )
            email_inp.send_keys(self.details.get("email", ""))
            self.driver.find_element(
                By.XPATH, "//button[contains(.,'Continue') or contains(.,'Sign in')]"
            ).click()
            time.sleep(2)
            pw = WebDriverWait(self.driver, WAIT).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "input[type='password']")
                )
            )
            pw.send_keys(self.details.get("password", ""))
            self.driver.find_element(
                By.XPATH, "//button[contains(.,'Sign in') or contains(.,'Log in')]"
            ).click()
            time.sleep(5)
            print("✅ Indeed: logged in")
        except Exception as e:
            print(f"    ⚠️  Indeed login issue: {e}")

    def click_apply(self) -> str:
        for sel in [
            "button[id*='applyButton']", "button[class*='apply']",
            "#applyButton", "a[id*='applyButton']",
        ]:
            try:
                el = WebDriverWait(self.driver, 6).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
                )
                txt = el.text.lower()
                if "apply" in txt:
                    self.safe_click(el)
                    return "clicked"
            except Exception:
                pass
        for xp in [
            "//button[contains(.,'Apply now')]",
            "//button[contains(.,'Apply')]",
            "//a[contains(.,'Apply')]",
        ]:
            try:
                el = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, xp))
                )
                txt = el.text.lower()
                if "applied" == txt.strip():
                    return "already_applied"
                if "apply" in txt:
                    self.safe_click(el)
                    time.sleep(2)
                    # New tab = company site
                    if len(self.driver.window_handles) > 1:
                        self.driver.switch_to.window(
                            self.driver.window_handles[-1]
                        )
                        url = self.driver.current_url
                        self.driver.close()
                        self.driver.switch_to.window(
                            self.driver.window_handles[0]
                        )
                        return url   # company site
                    return "clicked"
            except Exception:
                pass
        return "error"

    def _handle_indeed_step(self) -> str:
        """Handle one step/screen of the Indeed apply flow."""
        time.sleep(2)

        if self.is_success():
            return "done"
        if self.is_already_applied():
            return "already_applied"

        # Resume selection (Indeed shows existing resumes or upload)
        for sel in [
            "button[class*='resume'][class*='select']",
            "div[class*='resumePickerItem'] button",
            "label[class*='resume']",
        ]:
            try:
                els = [e for e in self.driver.find_elements(By.CSS_SELECTOR, sel)
                       if e.is_displayed()]
                if els:
                    self.safe_click(els[0])
                    print("    📄 Indeed: resume selected")
                    time.sleep(1)
                    break
            except Exception:
                pass

        # Upload if no existing
        self.try_upload_resume()

        # Fill form fields
        self.handle_generic_form_fields()

        # Continue / Next / Submit
        for btn_text in ["Submit your application", "Continue", "Next",
                         "Submit", "Apply"]:
            try:
                btn = self.driver.find_element(
                    By.XPATH,
                    f"//button[contains(normalize-space(.),'{btn_text}')]"
                )
                if btn.is_displayed() and btn.is_enabled():
                    self.safe_click(btn)
                    print(f"    ➡️  Clicked: {btn_text}")
                    return "continue"
            except Exception:
                pass
        return "stuck"

    def apply(self, url: str) -> str:
        try:
            self.driver.get(url)
            self.wait_page()

            if self.is_already_applied():
                return "already_applied"

            result = self.click_apply()
            if result == "already_applied":
                return "already_applied"
            if result == "error":
                return "error"
            if result.startswith("http"):
                return result   # company site

            for step in range(MAX_STEPS):
                outcome = self._handle_indeed_step()
                if outcome == "done":
                    return "applied"
                if outcome == "already_applied":
                    return "already_applied"
                if outcome == "stuck":
                    time.sleep(2)
                    if self.is_success():
                        return "applied"
                    return "error"
                time.sleep(1.5)

            return "applied" if self.is_success() else "error"

        except KeyboardInterrupt:
            raise
        except Exception as e:
            if _is_dead_session_error(e):
                # Don't swallow this — main()'s retry loop needs to see it
                # to restart the browser, instead of every remaining job
                # failing identically against a dead session.
                raise
            print(f"    ❌ Indeed error:\n{traceback.format_exc()}")
            return "error"


# ══════════════════════════════════════════════════════════════════════════════
#  GLASSDOOR PLATFORM
# ══════════════════════════════════════════════════════════════════════════════

class GlassdoorPlatform(BasePlatform):
    """
    Glassdoor job applications are mostly powered by Indeed or redirected to
    the company's ATS. We handle both paths.
    """

    def login(self):
        self.driver.get("https://www.glassdoor.com/profile/login_input.htm")
        try:
            WebDriverWait(self.driver, WAIT).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "input[name='username'],input[type='email']")
                )
            ).send_keys(self.details.get("email", ""))
            self.driver.find_element(
                By.CSS_SELECTOR, "input[name='password'],input[type='password']"
            ).send_keys(self.details.get("password", ""))
            self.driver.find_element(
                By.XPATH,
                "//button[contains(.,'Sign In') or contains(.,'Log In')]"
            ).click()
            time.sleep(5)
            print("✅ Glassdoor: logged in")
        except Exception as e:
            print(f"    ⚠️  Glassdoor login issue: {e}")

    def apply(self, url: str) -> str:
        try:
            self.driver.get(url)
            self.wait_page()
            time.sleep(3)

            if self.is_already_applied():
                return "already_applied"

            # Look for Apply button
            for xp in [
                "//button[contains(.,'Apply Now')]",
                "//button[contains(.,'Easy Apply')]",
                "//a[contains(.,'Apply')]",
                "//button[contains(.,'Apply')]",
            ]:
                try:
                    btn = WebDriverWait(self.driver, 5).until(
                        EC.element_to_be_clickable((By.XPATH, xp))
                    )
                    txt = btn.text.lower()
                    if "applied" == txt.strip():
                        return "already_applied"
                    self.safe_click(btn)
                    time.sleep(3)
                    break
                except Exception:
                    pass

            # New tab / redirect
            if len(self.driver.window_handles) > 1:
                self.driver.switch_to.window(self.driver.window_handles[-1])
                url2 = self.driver.current_url
                # If it's Indeed, hand off to Indeed platform logic
                if "indeed.com" in url2:
                    indeed = IndeedPlatform(self.driver, self.details)
                    return indeed.apply(url2)
                # Otherwise treat as company site
                self.driver.close()
                self.driver.switch_to.window(self.driver.window_handles[0])
                return url2

            if "indeed.com" in self.driver.current_url:
                indeed = IndeedPlatform(self.driver, self.details)
                return indeed._handle_indeed_step()

            # Glassdoor's own apply form (rare)
            for step in range(MAX_STEPS):
                if self.is_success():
                    return "applied"
                self.handle_generic_form_fields()
                # Next
                for btn_text in ["Submit", "Next", "Continue", "Apply"]:
                    try:
                        btn = self.driver.find_element(
                            By.XPATH,
                            f"//button[contains(.,'{btn_text}')]"
                        )
                        if btn.is_displayed() and btn.is_enabled():
                            self.safe_click(btn)
                            time.sleep(2)
                            break
                    except Exception:
                        pass

            return "applied" if self.is_success() else "error"

        except KeyboardInterrupt:
            raise
        except Exception as e:
            if _is_dead_session_error(e):
                # Don't swallow this — main()'s retry loop needs to see it
                # to restart the browser, instead of every remaining job
                # failing identically against a dead session.
                raise
            print(f"    ❌ Glassdoor error:\n{traceback.format_exc()}")
            return "error"


# ══════════════════════════════════════════════════════════════════════════════
#  COMPANY SITE HANDLER  –  generic best-effort ATS filler
# ══════════════════════════════════════════════════════════════════════════════

class CompanySiteHandler(BasePlatform):
    """
    Attempts to fill common ATS portals:
      Workday, Greenhouse, Lever, SmartRecruiters, Taleo, iCIMS, BambooHR
    Falls back to generic field detection.
    """

    ATS_HINTS = {
        "workday":          self._handle_workday   if False else None,
        "greenhouse":       "_handle_greenhouse",
        "lever":            "_handle_lever",
        "smartrecruiters":  "_handle_smartr",
        "taleo":            "_handle_generic",
        "icims":            "_handle_generic",
        "bamboohr":         "_handle_generic",
        "jobvite":          "_handle_generic",
        "successfactors":   "_handle_generic",
        "myworkday":        "_handle_workday",
    }

    def detect_ats(self) -> str:
        url = self.driver.current_url.lower()
        for key in self.ATS_HINTS:
            if key in url:
                return key
        # Check page source for hints
        try:
            src = self.driver.page_source.lower()
            for key in self.ATS_HINTS:
                if key in src:
                    return key
        except Exception:
            pass
        return "generic"

    def handle(self, url: str) -> str:
        """Main entry: navigate to URL and apply."""
        try:
            if url.startswith("http"):
                self.driver.get(url)
                self.wait_page()
                time.sleep(3)
            
            ats = self.detect_ats()
            print(f"    🏢 ATS detected: {ats}")

            handler_name = self.ATS_HINTS.get(ats, "_handle_generic")
            if isinstance(handler_name, str):
                handler = getattr(self, handler_name, self._handle_generic)
            else:
                handler = self._handle_generic

            return handler()

        except KeyboardInterrupt:
            raise
        except Exception as e:
            if _is_dead_session_error(e):
                # Don't swallow this — main()'s retry loop needs to see it
                # to restart the browser, instead of every remaining job
                # failing identically against a dead session.
                raise
            print(f"    ❌ Company site error:\n{traceback.format_exc()}")
            return "error"

    def _handle_workday(self) -> str:
        """Workday ATS — multi-step wizard."""
        for step in range(MAX_STEPS):
            time.sleep(2)
            if self.is_success():
                return "applied"

            self.try_upload_resume()
            self.handle_generic_form_fields()

            # Workday Next button
            for xp in [
                "//button[@data-automation-id='bottom-navigation-next-button']",
                "//button[contains(.,'Next')]",
                "//button[contains(.,'Continue')]",
                "//button[contains(.,'Submit')]",
                "//button[@aria-label='Submit']",
            ]:
                try:
                    btn = self.driver.find_element(By.XPATH, xp)
                    if btn.is_displayed() and btn.is_enabled():
                        self.safe_click(btn)
                        print(f"    ➡️  Workday step {step+1}")
                        break
                except Exception:
                    pass
        return "applied" if self.is_success() else "error"

    def _handle_greenhouse(self) -> str:
        """Greenhouse ATS."""
        time.sleep(2)
        self.try_upload_resume()
        self.handle_generic_form_fields()

        # Greenhouse: fill custom questions
        for section in self.driver.find_elements(
            By.CSS_SELECTOR, "li.custom-question, div.custom_fields fieldset"
        ):
            try:
                label_el = section.find_element(
                    By.CSS_SELECTOR, "label, legend"
                )
                label = label_el.text.strip()
                inps = section.find_elements(
                    By.CSS_SELECTOR, "input, textarea, select"
                )
                for inp in inps:
                    if not inp.is_displayed():
                        continue
                    if inp.tag_name == "select":
                        self.fill_select(inp, get_value(label, self.details), label)
                    else:
                        val = get_value(label, self.details) or "N/A"
                        self.fill_input(inp, val)
            except Exception:
                pass

        # Submit
        try:
            submit = self.driver.find_element(
                By.XPATH,
                "//input[@type='submit' and contains(@value,'Submit')]"
                " | //button[contains(.,'Submit Application')]"
            )
            self.safe_click(submit)
            time.sleep(3)
        except Exception:
            pass

        return "applied" if self.is_success() else "error"

    def _handle_lever(self) -> str:
        """Lever ATS."""
        time.sleep(2)
        self.try_upload_resume()
        self.handle_generic_form_fields()

        for xp in [
            "//button[contains(.,'Submit Application')]",
            "//input[@type='submit']",
        ]:
            try:
                btn = self.driver.find_element(By.XPATH, xp)
                if btn.is_displayed():
                    self.safe_click(btn)
                    time.sleep(3)
                    break
            except Exception:
                pass

        return "applied" if self.is_success() else "error"

    def _handle_smartr(self) -> str:
        """SmartRecruiters ATS."""
        for step in range(MAX_STEPS):
            time.sleep(2)
            if self.is_success():
                return "applied"
            self.try_upload_resume()
            self.handle_generic_form_fields()
            for xp in [
                "//button[contains(.,'Next')]",
                "//button[contains(.,'Send Application')]",
                "//button[contains(.,'Submit')]",
            ]:
                try:
                    btn = self.driver.find_element(By.XPATH, xp)
                    if btn.is_displayed() and btn.is_enabled():
                        self.safe_click(btn)
                        break
                except Exception:
                    pass
        return "applied" if self.is_success() else "error"

    def _handle_generic(self) -> str:
        """Generic multi-step form filler for unknown ATS."""
        for step in range(MAX_STEPS):
            time.sleep(2)
            if self.is_success():
                return "applied"
            if self.is_already_applied():
                return "already_applied"

            self.try_upload_resume()
            filled = self.handle_generic_form_fields()

            # Click next/submit
            advanced = False
            for btn_text in [
                "Submit Application", "Submit", "Apply",
                "Next", "Continue", "Save and Continue",
            ]:
                try:
                    btn = self.driver.find_element(
                        By.XPATH,
                        f"//button[contains(normalize-space(.),'{btn_text}')] "
                        f"| //input[@value='{btn_text}']"
                    )
                    if btn.is_displayed() and btn.is_enabled():
                        self.safe_click(btn)
                        print(f"    ➡️  Generic step {step+1}: {btn_text}")
                        advanced = True
                        break
                except Exception:
                    pass

            if not advanced and filled == 0:
                # Nothing to do — likely stuck or done
                break

        return "applied" if self.is_success() else "error"


# Fix the broken class reference in ATS_HINTS
CompanySiteHandler.ATS_HINTS = {
    "workday":         "_handle_workday",
    "greenhouse":      "_handle_greenhouse",
    "lever":           "_handle_lever",
    "smartrecruiters": "_handle_smartr",
    "taleo":           "_handle_generic",
    "icims":           "_handle_generic",
    "bamboohr":        "_handle_generic",
    "jobvite":         "_handle_generic",
    "successfactors":  "_handle_generic",
    "myworkday":       "_handle_workday",
}


# ══════════════════════════════════════════════════════════════════════════════
#  DRIVER FACTORY
# ══════════════════════════════════════════════════════════════════════════════

def make_driver() -> webdriver.Chrome:
    """Build a Chrome WebDriver instance.

    Selenium 4's bundled "Selenium Manager" (a Rust binary that auto-detects
    Chrome and downloads a matching chromedriver) is the default path, but it
    is known to crash on some Windows setups — commonly due to antivirus
    interference or a corrupted/quarantined selenium-manager.exe — with
    errors like "memory allocation ... failed" or NoSuchDriverException.

    To make auto-apply resilient to that, this tries several driver
    resolution strategies in order and only fails loudly if every one of
    them fails, with a message that tells you exactly what to try next.

    IMPORTANT: each attempt gets its own brand-new Options object. Reusing
    one Options instance across attempts is unsafe — undetected-chromedriver
    in particular mutates/patches whatever Options object it's given, which
    would silently corrupt a shared object and break a later attempt with
    confusing errors like "unrecognized chrome option: excludeSwitches".
    """
    from selenium.webdriver.chrome.service import Service

    import shutil
    import sys

    def is_chrome_available() -> bool:
        cp = os.environ.get("CHROMEDRIVER_PATH", "").strip()
        if cp and os.path.exists(cp):
            return True
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
            if shutil.which(name):
                return True
        if os.name == "nt":
            for p in (
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            ):
                if os.path.exists(p):
                    return True
        return False

    if not is_chrome_available():
        raise RuntimeError("Google Chrome binary not found on this system. (Cloud environment detected without Chrome)")

    def new_options(minimal: bool = False) -> Options:
        opts = Options()
        opts.add_argument("--disable-blink-features=AutomationControlled")
        if sys.platform != "win32" or os.environ.get("HEADLESS", "0") == "1":
            opts.add_argument("--headless=new")
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            opts.add_argument("--window-size=1920,1080")
        else:
            opts.add_argument("--start-maximized")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-extensions")
        opts.add_argument("--disable-background-networking")
        opts.add_argument("--disable-default-apps")
        opts.add_argument("--disable-sync")
        opts.add_argument("--metrics-recording-only")
        opts.add_argument("--mute-audio")
        opts.add_argument("--no-first-run")
        opts.add_argument("--disable-backgrounding-occluded-windows")
        opts.add_argument("--disable-renderer-backgrounding")
        if not minimal:
            try:
                opts.add_experimental_option("excludeSwitches", ["enable-automation"])
                opts.add_experimental_option("useAutomationExtension", False)
            except Exception:
                pass
        return opts

    errors = []

    # 1) Plain Selenium Manager first — simplest path, and works fine on
    #    most machines. Tried before the heavier fallbacks below.
    try:
        driver = webdriver.Chrome(options=new_options())
        driver.execute_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        return driver
    except Exception as e:
        errors.append(f"Selenium Manager (default): {e}")

    # 2) Explicit override — set CHROMEDRIVER_PATH if you've manually
    #    downloaded a chromedriver that matches your installed Chrome version
    #    (https://googlechromelabs.github.io/chrome-for-testing/).
    chromedriver_path = os.environ.get("CHROMEDRIVER_PATH", "").strip()
    if chromedriver_path:
        try:
            driver = webdriver.Chrome(service=Service(executable_path=chromedriver_path), options=new_options())
            driver.execute_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
            return driver
        except Exception as e:
            errors.append(f"CHROMEDRIVER_PATH ('{chromedriver_path}'): {e}")

    # 3) webdriver-manager — downloads a matching chromedriver itself via a
    #    plain Python HTTP request, bypassing Selenium Manager entirely.
    #    Requires `pip install webdriver-manager`.
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=new_options())
        driver.execute_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        return driver
    except Exception as e:
        errors.append(f"webdriver-manager: {e}")

    # 4) undetected-chromedriver — already a project dependency; manages its
    #    own driver resolution independently of Selenium Manager too.
    try:
        import undetected_chromedriver as uc
        driver = uc.Chrome(options=new_options(minimal=True))
        driver.execute_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        return driver
    except Exception as e:
        errors.append(f"undetected-chromedriver: {e}")

    error_report = "\n".join(f"  - {e}" for e in errors)
    raise RuntimeError(
        "Could not start Chrome via any driver resolution method:\n"
        f"{error_report}\n\n"
        "Things to try:\n"
        "  1. Make sure Google Chrome is installed and up to date.\n"
        "  2. pip install --upgrade webdriver-manager undetected-chromedriver\n"
        "  3. Temporarily disable antivirus/Windows Defender real-time "
        "protection and retry — selenium-manager.exe crashing with a "
        "'memory allocation failed' error is commonly caused by AV "
        "interference or a corrupted download of that binary.\n"
        "  4. Download a chromedriver matching your Chrome version from "
        "https://googlechromelabs.github.io/chrome-for-testing/ and set "
        "the CHROMEDRIVER_PATH environment variable to its full path.\n"
        "  5. If you're seeing 'tab crashed', 'paging file is too small', "
        "or other memory-allocation errors anywhere in the logs: increase "
        "your Windows virtual memory (pagefile) size — Settings > System > "
        "About > Advanced system settings > Performance > Settings > "
        "Advanced > Virtual memory > Change — and make sure the C: drive "
        "has several GB of free space for it to grow into. This is a "
        "Windows-level setting, not something this app can change for you."
    )


# ══════════════════════════════════════════════════════════════════════════════
#  EXCEL SAVE
# ══════════════════════════════════════════════════════════════════════════════

def save_excel(rows, fieldnames, path: str = OUTPUT_JOBS_FILE):
    for attempt in range(5):
        try:
            wb = Workbook()
            ws = wb.active
            ws.title = "Jobs"
            ws.append(fieldnames)
            for row in rows:
                ws.append([row.get(col, "") for col in fieldnames])
            for col_cells in ws.columns:
                max_len = max(
                    (len(str(c.value)) for c in col_cells if c.value),
                    default=10
                )
                ws.column_dimensions[
                    col_cells[0].column_letter
                ].width = min(max_len + 4, 50)
            wb.save(path)
            wb.close()
            print(f"    💾 Saved → {path}")
            return True
        except PermissionError:
            print(f"    ⚠️  File locked — retry {attempt+1}/5…")
            time.sleep(attempt + 1)
        except Exception as e:
            print(f"    ❌ Save error: {e}")
            return False
    return False


# ══════════════════════════════════════════════════════════════════════════════
#  ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════════

PLATFORM_CLASSES = {
    "naukri":    NaukriPlatform,
    "linkedin":  LinkedInPlatform,
    "indeed":    IndeedPlatform,
    "glassdoor": GlassdoorPlatform,
}


def _is_dead_session_error(exc: Exception) -> bool:
    """True if this exception means the Chrome session itself died (crashed,
    was closed, lost its DevTools connection) rather than a normal
    page/element-level failure. These need a browser restart, not just a
    per-job retry — continuing to use a dead session fails instantly and
    identically for every remaining job."""
    msg = str(exc).lower()
    signals = (
        "invalid session id",
        "session deleted",
        "not connected to devtools",
        "chrome not reachable",
        "disconnected: not connected",
        "target window already closed",
        "no such window",
        "chrome failed to start",
    )
    return any(s in msg for s in signals)


MAX_BROWSER_RESTARTS = 2


def main():
    details = load_details()
    print(f"👤 Loaded profile: {details.get('name', '?')}")

    driver = make_driver()

    # Initialise platforms (lazy login)
    platforms: dict = {}
    logged_in: set  = set()

    def get_platform(name: str):
        if name not in PLATFORM_CLASSES:
            return None
        if name not in platforms:
            platforms[name] = PLATFORM_CLASSES[name](driver, details)
        return platforms[name]

    def ensure_login(name: str):
        if name in logged_in:
            return
        p = get_platform(name)
        if p and hasattr(p, "login"):
            p.login()
            logged_in.add(name)

    login_failed_platforms: set = set()

    company_handler = CompanySiteHandler(driver, details)

    browser_restarts = {"count": 0}

    def restart_browser():
        """Rebuild the Chrome session after a mid-run crash. Mutates driver/
        company_handler in main()'s own scope (fine — no nonlocal needed,
        we're not inside a nested function here) and clears platforms/
        logged_in in place so get_platform()/ensure_login() naturally pick
        up fresh objects bound to the new driver on their next call."""
        nonlocal driver, company_handler
        browser_restarts["count"] += 1
        print(f"    🔄 Browser session died — restarting Chrome (attempt {browser_restarts['count']}/{MAX_BROWSER_RESTARTS})…")
        log_event(stage="BROWSER_RESTART", attempt=browser_restarts["count"])
        try:
            driver.quit()
        except Exception:
            pass
        driver = make_driver()
        platforms.clear()
        logged_in.clear()
        company_handler = CompanySiteHandler(driver, details)

    try:
        # ── Load jobs workbook ─────────────────────────────────────────────────
        wb = load_workbook(JOBS_FILE)
        ws = wb.active
        fieldnames = [str(c.value) for c in ws[1] if c.value]
        rows = [
            dict(zip(fieldnames, [str(v) if v else "" for v in row]))
            for row in ws.iter_rows(min_row=2, values_only=True)
        ]

        for col in ["Status", "Remark", "Applied Status",
                    "Applied Date", "Company Site URL",
                    "External Portal", "External ATS",
                    "External Domain", "Redirect URL"]:
            if col not in fieldnames:
                fieldnames.append(col)

        today = date.today().strftime("%d-%m-%Y")

        # ── Iterate jobs ───────────────────────────────────────────────────────
        for row in rows:
            status   = (row.get("Status") or "").strip().lower()
            platform = (row.get("Platform") or "").strip().lower()
            url      = (row.get("URL") or "").strip()
            title    = row.get("Title", "")
            company  = row.get("Company", "")

            if status != "new":
                continue
            if not url:
                continue

            plat_obj = get_platform(platform)
            if not plat_obj and platform not in ["company_site", "other", ""]:
                print(f"⏭️  Unknown platform '{platform}': {row.get('Title')}")
                continue

            print(f"\n{'='*65}")
            print(f"🔍  {row.get('Title')} @ {row.get('Company')}  [{platform}]")
            print(f"    {url}")
            log_event(stage="OPEN_JOB", company=company, title=title,
                      source=platform, original_url=url)

            # Login if needed
            if platform in PLATFORM_CLASSES:
                if platform in login_failed_platforms:
                    row["Status"]        = "Error"
                    row["Remark"]        = f"Skipped — {platform} login already failed earlier this run"
                    row["Applied Status"] = "Error"
                    print(f"    ⏭️  Skipping — {platform} login already failed this run")
                    log_event(stage="SKIPPED_LOGIN_FAILED", company=company, title=title,
                              source=platform, original_url=url)
                    save_excel(rows, fieldnames)
                    continue
                try:
                    login_attempt = 0
                    while True:
                        try:
                            ensure_login(platform)
                            break
                        except Exception as login_error:
                            if not _is_dead_session_error(login_error) or browser_restarts["count"] >= MAX_BROWSER_RESTARTS or login_attempt >= 1:
                                raise
                            login_attempt += 1
                            print("    ⚠️  Chrome died during login; restarting and retrying login…")
                            log_event(stage="LOGIN_SESSION_LOST", company=company,
                                      title=title, source=platform,
                                      original_url=url)
                            restart_browser()
                            plat_obj = get_platform(platform)
                except KeyboardInterrupt:
                    raise
                except Exception:
                    tb = traceback.format_exc()
                    print(f"    ❌ Login failed for {platform}:\n{tb}")
                    log_event(stage="LOGIN_FAILED", company=company, title=title,
                              source=platform, original_url=url,
                              exception=tb.strip().splitlines()[-1] if tb else "",
                              traceback=tb)
                    login_failed_platforms.add(platform)
                    row["Status"]        = "Error"
                    row["Remark"]        = f"{platform} login failed — see apply_events.jsonl for details"
                    row["Applied Status"] = "Error"
                    try:
                        driver.switch_to.window(driver.window_handles[0])
                    except Exception:
                        pass
                    save_excel(rows, fieldnames)
                    continue

            # Apply — with one retry-after-restart if the browser itself died
            result = "error"
            for attempt in range(2):
                try:
                    if plat_obj:
                        result = plat_obj.apply(url)
                    else:
                        # Direct company site
                        result = company_handler.handle(url)
                    break
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    if _is_dead_session_error(e):
                        if browser_restarts["count"] < MAX_BROWSER_RESTARTS and attempt == 0:
                            restart_browser()
                            plat_obj = get_platform(platform)
                            try:
                                ensure_login(platform)
                            except Exception:
                                pass
                            continue  # retry this same job once with the fresh browser

                        tb = traceback.format_exc()
                        print(f"    ❌ Browser session lost (restart budget {browser_restarts['count']}/{MAX_BROWSER_RESTARTS} used):\n{tb}")
                        log_event(stage="BROWSER_SESSION_LOST", company=company, title=title,
                                  source=platform, original_url=url, exception=str(e))
                        result = "error"
                        if browser_restarts["count"] >= MAX_BROWSER_RESTARTS:
                            # No restart budget left — every remaining job would fail
                            # identically and instantly against this dead session, so
                            # stop the whole run here instead of burning through them.
                            row["Status"]        = "Error"
                            row["Remark"]        = "Browser session crashed repeatedly — auto-apply stopped early"
                            row["Applied Status"] = "Error"
                            save_excel(rows, fieldnames)
                            print("🛑 Stopping run — Chrome kept crashing and the restart budget is exhausted.")
                            print("    This is very likely a low-memory/pagefile issue on this machine, not an app bug —")
                            print("    see the README's Troubleshooting section for how to fix it.")
                            return
                        break  # give up on just this job, move on — budget remains for later crashes
                    tb = traceback.format_exc()
                    print(f"    ❌ Unexpected error:\n{tb}")
                    log_event(stage="ERROR", company=company, title=title,
                              source=platform, original_url=url,
                              exception=tb.strip().splitlines()[-1] if tb else "",
                              traceback=tb)
                    result = "error"
                    break

            # Switch back to main window
            try:
                driver.switch_to.window(driver.window_handles[0])
            except Exception:
                pass
            time.sleep(2)

            # ── Record result ──────────────────────────────────────────────────
            if result == "applied":
                row["Status"]        = "Applied"
                row["Remark"]        = "Applied Successfully"
                row["Applied Status"] = "Applied"
                row["Applied Date"]  = today
                row["Company Site URL"] = ""
                print("    ✅ → Applied")
                log_event(stage="APPLIED", company=company, title=title,
                          source=platform, original_url=url)

            elif result == "already_applied":
                row["Status"]        = "Already Applied"
                row["Remark"]        = "Applied Earlier"
                row["Applied Status"] = "Already Applied"
                row["Applied Date"]  = today
                print("    ♻️  → Already Applied")
                log_event(stage="ALREADY_APPLIED", company=company, title=title,
                          source=platform, original_url=url)

            elif result and result.startswith("http"):
                # Redirected off-platform to an external ATS / company site.
                redirected_url = result
                ats    = detect_ats_from_url(redirected_url)
                domain = urlparse(redirected_url).netloc

                log_event(stage="EXTERNAL_PORTAL_DETECTED", company=company,
                          title=title, source=platform, original_url=url,
                          redirected_url=redirected_url, ats=ats, domain=domain)

                if EXTERNAL_PORTAL_MODE == "attempt":
                    # Legacy behaviour: try the best-effort generic/ATS filler.
                    row["Status"]        = "Company Site"
                    row["Remark"]        = "Needs Manual Apply"
                    row["Applied Status"] = "Company Site"
                    row["Applied Date"]  = today
                    row["Company Site URL"] = redirected_url
                    print(f"    🔗 → Company Site: {redirected_url}")
                    print("    🏢 Attempting company site auto-fill…")
                    cs_result = company_handler.handle(redirected_url)
                    if cs_result == "applied":
                        row["Status"]        = "Applied"
                        row["Remark"]        = "Applied via Company Site"
                        row["Applied Status"] = "Applied"
                        print("    ✅ → Company Site Applied")
                        log_event(stage="APPLIED", company=company, title=title,
                                  source=platform, original_url=url, ats=ats)
                else:
                    # Recommended, reliable path: capture and skip.
                    row["Status"]           = "External Portal"
                    row["Remark"]           = f"Redirected to {ats} ({domain}) — needs manual application"
                    row["Applied Status"]   = "External Portal"
                    row["Applied Date"]     = today
                    row["Company Site URL"] = redirected_url
                    row["External Portal"]  = "True"
                    row["External ATS"]     = ats
                    row["External Domain"]  = domain
                    row["Redirect URL"]     = redirected_url
                    print(f"    🌐 → External Portal detected: {ats} ({domain}) — skipping, needs manual apply")
                    print(f"        {redirected_url}")

            else:
                row["Status"]        = "Error"
                row["Remark"]        = "Application Failed"
                row["Applied Status"] = "Error"
                print("    ❌ → Error")
                log_event(stage="ERROR", company=company, title=title,
                          source=platform, original_url=url)

            save_excel(rows, fieldnames)

        print(f"\n{'='*65}")
        print("✅ All done.")
        save_excel(rows, fieldnames)

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
