# Multi-Platform Job Auto-Apply Bot

Automates job applications across **Naukri, LinkedIn, Indeed, Glassdoor** and
**Company ATS portals** (Workday, Greenhouse, Lever, SmartRecruiters, and more).

---

## Folder structure

```
job_bot/
├── auto_apply_bot.py   ← Main script
├── details.xlsx        ← Your profile (fill this once)
├── jobs.xlsx           ← Job list (add URLs + set Status = New)
├── jobs_output.xlsx    ← Auto-created: progress tracking
└── README.md           ← This file
```

---

## Quick Start

### 1. Install dependencies

```bash
pip install selenium openpyxl
```

Download **ChromeDriver** matching your Chrome version:
https://chromedriver.chromium.org/downloads

Add `chromedriver` to your PATH, or place it in the same folder.

### 2. Fill `details.xlsx`

Open `details.xlsx` and complete **row 2** with your information.  
Key columns:

| Column | Example | Used for |
|--------|---------|----------|
| `name` | Bablu Kumar | Name fields |
| `email` | you@gmail.com | Login + email questions |
| `password` | YourPass123 | Login to all platforms |
| `phone` | 9876543210 | Phone number fields |
| `current_location` | Bangalore | Location questions |
| `preferred_location` | Bangalore, Pune | Relocation questions |
| `current_employer` | ABC Tech | Current company fields |
| `current_ctc` | 8 LPA | Salary questions |
| `expected_ctc` | 14 LPA | Expected salary fields |
| `notice_period` | Immediate | Notice period questions |
| `total_experience` | 3 | Years of experience |
| `relevant_experience` | 2 | Domain-specific exp |
| `highest_qualification` | B.Tech CS | Education questions |
| `skills` | Python, SQL, ML | Skills summary |
| `sql_experience` | 3 | SQL-specific exp (years) |
| `python_experience` | 3 | Python-specific exp |
| `powerbi_experience` | 2 | Power BI exp |
| `work_authorization` | Yes - Authorized | Visa/work auth questions |
| `linkedin_url` | linkedin.com/in/... | LinkedIn profile field |
| `resume_path` | C:/Users/.../resume.pdf | Resume upload (full path) |
| `cover_letter` | I am a data analyst... | Cover letter fields |

### 3. Fill `jobs.xlsx`

Add one row per job. Mandatory columns:

| Column | Values |
|--------|--------|
| `Title` | Job title (for your reference) |
| `Company` | Company name (for your reference) |
| `Platform` | `naukri` / `linkedin` / `indeed` / `glassdoor` / `company_site` |
| `URL` | Full job URL |
| `Status` | Set to `New` to apply; bot changes it after |

### 4. Run

```bash
python auto_apply_bot.py
```

The script opens Chrome, logs in to each platform, applies, and updates
`jobs_output.xlsx` after every job.

---

## Platform Details

### Naukri
- Logs in with email + password
- Clicks "Apply" button (by ID, CSS, XPath fallback)
- Handles the chatbot panel: radio questions, text inputs, contenteditable divs
- Detects "Apply on Company Site" and routes to CompanySiteHandler
- Saves progress to CSV after every job

**Chatbot questions handled:**
- Notice period, total/relevant experience, current/expected CTC
- Current and preferred location, qualification, employer, phone
- Skill-specific experience (SQL, Python, Power BI, Excel, ML)

### LinkedIn (Easy Apply only)
- Logs in with email + password
- Detects "Easy Apply" button (non-Easy Apply jobs → marked as Company Site)
- Handles multi-page modal: text inputs, dropdowns, typeahead/combobox, radios
- Auto-fills location combobox (selects first suggestion)
- Ticks EEO/consent checkboxes automatically
- Clicks: Next → Review → Submit Application

**Form fields handled:**
- Name, email, phone, address/location
- Current/expected salary, experience, notice period
- LinkedIn URL, portfolio, GitHub
- Yes/No questions (work authorization, etc.)
- EEO voluntary disclosures

### Indeed
- Logs in to secure.indeed.com
- Selects existing resume or uploads new one
- Handles multi-step application form
- New-tab detection → routes to CompanySiteHandler
- Clicks: Continue → Submit your application

### Glassdoor
- Logs in to glassdoor.com
- Detects Indeed-backed applications and delegates to IndeedPlatform
- Handles new-tab redirects to company ATS

---

## Company Site Handler (ATS Auto-fill)

When a job redirects to a company portal, the bot tries to fill it automatically.

### Detected ATS portals

| ATS | Handler | Notes |
|-----|---------|-------|
| **Workday** | Dedicated | Multi-step wizard, data-automation-id buttons |
| **Greenhouse** | Dedicated | Custom questions section, `<fieldset>` labels |
| **Lever** | Dedicated | Single-page form, Submit Application button |
| **SmartRecruiters** | Dedicated | Multi-step, Send Application button |
| **Taleo** | Generic | Label-based field detection |
| **iCIMS** | Generic | Label-based field detection |
| **BambooHR** | Generic | Label-based field detection |
| **Jobvite** | Generic | Label-based field detection |
| **SAP SuccessFactors** | Generic | Label-based field detection |
| **Unknown** | Generic | Universal fallback |

### Generic field detection strategy

For each form field, the bot reads its label using (in priority order):
1. `aria-label` attribute
2. `placeholder` attribute
3. `<label for="id">` element
4. `aria-labelledby` reference
5. `name` attribute (reformatted)

Then maps the label text to your `details.xlsx` profile.

### Field types handled automatically
- `<input type="text/email/tel/number">`
- `<textarea>`
- `<select>` dropdowns (best-match option selected)
- `<input type="radio">` groups
- `<input type="checkbox">` (agreement/EEO checkboxes)
- `<input type="file">` (resume upload)
- `contenteditable` divs (Naukri chatbot style)
- Typeahead / combobox (LinkedIn style)

---

## Question Matching Logic

The bot maps every question to a `details.xlsx` field using keyword matching.
More-specific keywords are checked first.

**Auto Yes/No:**
- Yes → "authorized to work", "eligible", "18 years", "agree", "willing to"
- No  → "criminal", "require visa sponsorship", "convicted"

**Numeric fallback:** If no match is found for a number question, defaults to `"3"`.

**Dropdown best-match:** Tries exact match → substring → numeric → first non-skip option.

---

## Output: `jobs_output.xlsx`

After each job, the bot writes these columns:

| Column | Values |
|--------|--------|
| `Status` | Applied / Already Applied / Company Site / Error |
| `Remark` | Human-readable note |
| `Applied Status` | Same as Status |
| `Applied Date` | DD-MM-YYYY |
| `Company Site URL` | URL of the ATS portal (if redirected) |

---

## Adding More Platforms

1. Create a new class inheriting from `BasePlatform`
2. Implement `login(self)` and `apply(self, url: str) -> str`
3. Return: `"applied"`, `"already_applied"`, `"error"`, or an `http://...` URL
4. Add to `PLATFORM_CLASSES` dict in `main()`

---

## Tips

- **Slow down if getting CAPTCHA:** Increase `STEP_DELAY` and `WAIT` in CONFIG
- **Resume path:** Use the full absolute path, e.g. `C:\Users\Bablu\resume.pdf`
- **LinkedIn:** Works only for "Easy Apply" jobs; non-Easy Apply → recorded as Company Site
- **CTC format:** Enter as you want it typed, e.g. `8 LPA` or `800000` or `8,00,000`
- **Run in non-headless mode** (default) so you can watch and intervene if needed
- **CAPTCHA:** The bot will pause — solve manually and it will continue

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| "No Apply button found" | Check if job URL is correct and job is still open |
| Bot stuck on same question | Increase `MAX_STEPS`; check `details.xlsx` has the field |
| ChromeDriver version mismatch | Download matching version from chromedriver.chromium.org |
| LinkedIn 2FA | Complete 2FA manually once; session usually persists |
| Excel file locked | Close `jobs_output.xlsx` before running; bot retries 5× |
