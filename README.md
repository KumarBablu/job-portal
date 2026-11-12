# Personal Job Portal — AI Job Assistant

A personal job search command center built with Python and Flask.
It scrapes jobs from multiple platforms, scores every listing against your
profile, auto-applies using your saved answers, and tracks the whole pipeline
through a colorful local web dashboard. The Question Bank automatically learns
questions asked by Naukri's application chatbot, lets you correct each answer,
and reuses saved answers on future applications.

## Troubleshooting

**"Unable to obtain driver for chrome" / `NoSuchDriverException` /
`memory allocation ... failed` when running auto-apply or a Selenium
scraper fallback:**

This is Selenium Manager crashing, not this project's code. Try, in order:

1. `pip install --upgrade webdriver-manager undetected-chromedriver` so the
   app can fall back to a different driver-resolution method automatically
   (if you see `No module named 'webdriver_manager'` in the log, this is
   the fix — the package just isn't installed yet).
2. Temporarily disable antivirus/Windows Defender real-time protection and
   retry — this is the most common cause on Windows.
3. Make sure Google Chrome itself is installed and up to date.
4. As a last resort, download a chromedriver matching your Chrome version
   from https://googlechromelabs.github.io/chrome-for-testing/ and set the
   `CHROMEDRIVER_PATH` environment variable to its full path before running
   `python run.py`.

If you see an error like `unrecognized chrome option: excludeSwitches` or
`invalid argument: cannot parse capability`, that was a bug in an earlier
build of this fallback chain (a shared Chrome Options object was getting
mutated by one driver-resolution attempt and corrupting the next one) — it's
fixed now; every attempt builds its own independent Options object.

**Server crashes entirely with `memory allocation of N bytes failed` and a
`RUST_BACKTRACE` note, during AI scoring instead of during Chrome startup:**
Same root cause as the Chrome driver issue above, different Rust binary —
this time it's `hf_xet`, the accelerated Rust-based download backend
`sentence-transformers`/`huggingface_hub` uses the first time it downloads
the local scoring model. On a memory-constrained machine it can crash the
same way Selenium Manager does. Fixed in two ways:
1. The app now disables `hf_xet` and the older `hf_transfer` backend by
   default, forcing the plain, memory-light Python downloader instead
   (`HF_HUB_DISABLE_XET=1` / `HF_HUB_ENABLE_HF_TRANSFER=0`, both set
   automatically — no action needed).
2. A pre-flight memory check (via `psutil`, already in requirements.txt)
   skips attempting the model download at all when available memory is
   critically low, falling back to keyword-only scoring instead of risking
   the crash.
3. **Scraped jobs are now saved to disk and synced to the database *before*
   AI scoring runs**, not after — previously, if scoring crashed the whole
   process (as in the case above), the entire scrape's results were lost
   with it. Now the worst case is just missing AI scores, which get filled
   in automatically the next time you scrape or restart the app.

If this still happens on your machine, it's a strong sign the system itself
is critically low on memory (the same Werkzeug `MemoryError` mentioned above
tends to show up around the same time) — closing other applications,
checking free disk space (a nearly-full drive can also cause Windows virtual
memory commit failures that look like this), and restarting the machine are
worth trying.

**Confirmed root cause: Windows pagefile / virtual memory is too small.**
If your log ever shows the literal line `The paging file is too small for
this operation to complete. (os error 1455)`, or Chrome reports `tab
crashed`, that is Windows itself refusing to grant memory to a process —
not a bug in this app. Every one of the memory-allocation crashes described
above (Selenium Manager, `hf_xet`, Chrome's own tab process,
`undetected-chromedriver`) can all be traced back to the same underlying
cause on a given machine. Two things were done about it:
1. **Chrome now launches with a set of memory-frugal flags**
   (`--disable-gpu`, `--disable-dev-shm-usage`, `--disable-extensions`,
   and several others) on every driver-resolution attempt, reducing how
   much memory Chrome itself asks for.
2. A second real bug was found and fixed alongside this: `undetected-chromedriver`'s
   patched driver rejects the `excludeSwitches`/`useAutomationExtension`
   capabilities on some newer Chrome versions (`unrecognized chrome option:
   excludeSwitches`) — it doesn't need them anyway, since it does its own
   anti-detection patching, so that attempt now uses a minimal options set.

**But the actual fix has to happen in Windows, not in this app.** If you
keep seeing memory-allocation failures, increase your pagefile size:
`Settings > System > About > Advanced system settings > Performance >
Settings > Advanced > Virtual memory > Change` — untick "Automatically
manage paging file size for all drives", select your C: drive, choose
"Custom size", and set a reasonably large initial/maximum size (or just
re-tick "Automatically manage" and make sure the C: drive has several GB
of free space, since Windows needs room to grow the pagefile into). No
amount of retry logic in this app can substitute for Windows having enough
virtual memory to hand a process — every fallback here can only get you as
far as your system's actual memory ceiling allows.

**`RuntimeError: can't start new thread`, `OpenBLAS error: Memory allocation
still failed after 10 retries`, or auto-apply exits immediately after
"Starting auto-apply" with no jobs processed:**
This is the most severe form of the same memory story above — it means
Windows refused to let the *Flask server itself* create a new thread to
handle a request, which only happens when a system is critically out of
memory. No retry logic anywhere can work around this; it's an OS-level
resource ceiling, not an app bug. One real contributor was found and fixed:
the AI scoring model, once loaded during a scrape, was staying cached in
the Flask server's memory for the rest of the process's life — quietly
eating into the headroom available for whatever ran next, most commonly
the Chrome-based auto-apply bot immediately afterward. The model's memory
is now released right after each scoring batch finishes (it reloads in a
few seconds next time it's needed — no re-download, just held in memory
briefly less often). Live-log polling was also slowed slightly (1.5s → 2.5s)
so the dev server has to spin up fewer request-handling threads per second
while a heavy operation is running. Both help, but if you're hitting this,
it's a strong sign the **pagefile fix above is the one that actually
matters** — everything else here just reduces how hard the app pushes
against a ceiling that's fundamentally a Windows setting.

**Browser-crash recovery wasn't actually triggering, even though the logic
was correct:** each platform class (`NaukriPlatform`, `LinkedInPlatform`,
`IndeedPlatform`, `GlassdoorPlatform`, `CompanySiteHandler`) has its own
internal `except Exception` inside `apply()` that catches everything,
prints it, and returns `"error"` — including dead-session crashes. That
swallowed the exception before it ever reached `main()`'s restart-and-retry
logic, so every remaining job failed identically against the dead session
with a full duplicate stack trace, exactly as if the recovery code didn't
exist. Fixed: all five of these handlers now re-raise dead-session errors
specifically (checked via the same `_is_dead_session_error()` used by the
restart logic) instead of swallowing them, so `main()` actually gets the
chance to restart Chrome and retry. Verified with a simulation mirroring
the real class structure before packaging.

If a run like this leaves a batch of jobs marked "Error" that were never
really attempted, use the **Auto-Apply Filters** panel's "Error (retry)"
checkbox (alongside or instead of "New") to re-run just those.

**Chrome crashed mid-run and every remaining job failed instantly with
`InvalidSessionIdException` / "session deleted" / "not connected to
DevTools":** this means the browser itself died partway through a batch
(most commonly from the same memory pressure described above) — previously
the bot had no way to notice this and just kept hammering the dead session
for every remaining job, producing a wall of duplicate stack traces and
zero results. It now detects this specifically, restarts Chrome (up to 2
times per run) and retries, re-logging into whatever platform is needed on
the fresh session. If a particular job still fails right after a restart,
it's marked `Error` and the run moves on to the next job — the restart
budget isn't wasted on one stubborn job. Only once genuinely exhausted
(2 restarts used up) does the run stop early rather than continuing to
burn through a dead session.

- **Already-applied jobs were sometimes wrongly marked "Error"** — Naukri's
  UI shows an already-applied state in a few different ways (button text
  variants, a disabled button, or no Apply button rendered at all), and the
  bot's detection only caught the most common one. Broadened the phrase/
  text matching and added a fallback re-check before giving up, so these
  jobs are now correctly tagged "Already Applied" instead of "Error" /
  "Application Failed".
- **Auto-Apply Summary panel** — after a run, a dedicated summary shows a
  full status breakdown (New/Applied/Already Applied/External Portal/Error
  counts as chips) and a scrollable table of every job's outcome + remark
  from that run, with a single **"Download all results (with remarks)"**
  button — one file covering every status, instead of downloading a
  separate file per status. This also persists if you navigate away and
  back to the Auto-Apply tab.
- **Outreach queue was reading from the wrong source** — it queried the
  SQLite database directly, while every other part of the app treats
  `jobs_data.json` as the primary source of truth with the database as a
  synced copy. If the two ever drifted apart (e.g. right after a fresh
  `init_db.py`, or the DB lagging behind a manual edit to the JSON file),
  the outreach queue would show zero candidates even with plenty of
  high-scoring jobs. Fixed to read from JSON first, matching the Jobs tab
  and every other endpoint, with the database query kept only as a fallback.

**A single login timeout used to crash the entire auto-apply batch:**
Earlier builds let an exception from `ensure_login()` (e.g. a Naukri page
timing out while checking login state) propagate all the way up and kill
the whole run — even jobs on other platforms never got attempted. This is
fixed: a login failure for a platform is now caught, logged, and that job is
marked `Error`; every other remaining job for that platform is skipped
immediately (instead of retrying and timing out again) while jobs on other
platforms continue normally. Check `data/logs/apply_events.jsonl` for the
`LOGIN_FAILED` event and full traceback if this happens to you.

**`MemoryError` inside Werkzeug's dev server (`self.rfile.read(...)`):**
This is the built-in Flask development server (not this project's code)
failing to allocate memory for reading a request while the machine is under
memory pressure — commonly from several Chrome/chromedriver processes
running at once, especially across repeated auto-apply runs. The login-crash
fix above should reduce how often this happens (fewer runaway retries
spinning up extra browser processes). If it persists: close other
memory-heavy apps before running auto-apply, and consider setting
`debug=False` in `run.py`'s `app.run(...)` call once you're done actively
developing, since Flask's debug mode keeps extra state in memory per request.

## What's new in this rebuild

- **Personal Job Portal UI** — a single-page colorful dashboard (data-dense,
  fully dynamic) replacing the old interface: sidebar navigation, a live
  Dashboard, a filterable/sortable Jobs table with a card view, a slide-in
  job drawer, live scan/apply consoles, and a dedicated Applicant Profile page.
- **Required-fields auto-apply** — a new profile-driven feature that:
  - detects which fields a job's application form is likely to ask for
    (title/description keyword matching against a shared field catalog),
  - checks those fields against your saved **Applicant Profile**,
  - shows a readiness check ("Ready to apply" vs "Missing: expected_ctc, …")
    per job or across every "New" job at once, and
  - lets you edit all of those answers from a friendly web form instead of
    hand-editing a spreadsheet.
- **Apply to specific Job IDs** — paste a list of Job IDs or URLs (one per
  line, or comma-separated) on the Auto-Apply tab and run the bot against
  exactly that list, instead of only "all New jobs" or a Jobs-tab selection.
- **External-portal detection ("redirect and skip")** — when Naukri/LinkedIn/
  Indeed/Glassdoor redirects an application to an external ATS or company
  career site (Workday, Lever, Greenhouse, SuccessFactors, Taleo, Oracle,
  iCIMS, SmartRecruiters, Jobvite, BambooHR, JobDiva, Workable, and more),
  the bot no longer guesses at an unfamiliar form. It captures the company,
  title, source, original URL, redirect URL, detected ATS name, and a
  timestamp; marks the job **"External Portal"**; and moves on to the next
  job. A dedicated **External Portals** panel on the Auto-Apply tab lists
  every one of these with a direct link, so you can finish them yourself.
  (Set `EXTERNAL_PORTAL_MODE=attempt` as an environment variable to restore
  the old best-effort auto-fill behaviour instead.)
- **Structured apply logs** — every apply run now also writes
  `data/logs/apply_events.jsonl` (one JSON event per line: stage, company,
  title, source, URL, redirect URL, ATS, exception) alongside the existing
  human-readable console log, for easier diagnosis of failures.
- **Real upsert, not silent no-op** — re-scraping a job you've already seen
  now actually refreshes its score/description/skills in the database. Your
  tracked fields (status, notes, contact, interview date, outreach) are
  never overwritten by a re-scrape.
- **Fixed AI scoring** — the batch scorer's actual scoring loop had been
  left commented out, so every scrape silently scored 0 jobs. It's been
  rewritten to batch-encode all job listings in one pass (fast), blend
  semantic similarity with a resume-driven keyword/skill overlap score and
  an experience-range fit bonus/penalty, and produce a human-readable
  "AI Reason" per job — entirely offline, no API key.
- **Generate outreach by Job ID** — a direct lookup box on the Outreach tab:
  paste a Job ID or URL and generate the full outreach pack (connection
  note, InMail, cold email, follow-up) without opening the Jobs tab.
- **Filtered Excel export** — the "Export filtered" button on the Jobs tab
  downloads exactly what's on screen: current search text, platform, status,
  location, minimum score, and/or your manual row selection are all passed
  through as query params and applied server-side before the file is built.
- **Ollama removed** — every AI text feature (job scoring, cover letters,
  resumes, interview prep, gap analysis, outreach copy) now runs fully
  offline using local sentence-transformer embeddings plus deterministic
  templates. There is no external AI service call, no local server to start,
  and no API key to configure.
- **Cleanup** — removed the duplicate `Apply_module/` copy of the auto-apply
  bot and ad-hoc debugging scripts that weren't part of the app itself.
- **Dashboard/Jobs never updated after a run finished** — the live-log
  polling loop silently stopped forever the moment a single request hiccup
  happened (e.g. a transient dev-server error), so the UI never learned the
  scrape or auto-apply run had actually completed. Polling now tolerates
  transient failures and only gives up after several in a row, with a
  background reconnect check afterward so the dashboard still recovers on
  its own instead of requiring a manual page refresh.
- **More Jobs filters, including a date range** — the status filter now
  includes every status the app actually produces (`Already Applied`,
  `External Portal`, `Error`, not just the original five), and there's a
  new "Scraped" date-range filter plus a one-click "Clear filters" button.
  Both flow through to the filtered Excel export too.
- **Long lists now scroll in a fixed-height box** instead of stretching the
  page indefinitely — Top Matches, the required-fields readiness list, the
  External Portals queue, the Outreach queue, and the main Jobs table itself
  (which can hold 1000+ rows) all scroll within a bounded panel now.
- **Auto-Apply Filters** — a new panel on the Auto-Apply tab lets you choose
  exactly which jobs a run should target: job boards (Naukri/LinkedIn/
  Indeed/Glassdoor), status to include (New, or Error to retry failed
  ones), a scraped-date range, a minimum AI score, and a location filter —
  with a live "N jobs match these filters" count that updates as you adjust
  them. This sits alongside the existing "specific Job IDs" and Jobs-tab
  row-selection methods; all three now dispatch to the same backend
  selection logic.
- **Live auto-apply log moved to the end of the page** — matching the
  Scrape tab's layout (options/config first, live log last), instead of
  being buried in the middle of the Auto-Apply tab.
- **One-click "Download results" on the Auto-Apply tab** — after a run,
  download an Excel file for exactly one outcome — Applied Successfully,
  Already Applied, Needs Manual Apply (External Portal), Errors, Not Yet
  Applied (New), or everything — without needing to set filters manually on
  the Jobs tab. Live counts for each category are shown right above the
  buttons, and a category with zero jobs shows a message instead of
  downloading an empty file.
- **AI scoring model download crash fixed, and scrape data-loss prevented**
  — the first-time model download used a Rust-based fast-download backend
  that could crash the whole server on memory-constrained machines (same
  failure signature as the Chrome driver issue below). It's now disabled by
  default in favor of a slower-but-reliable plain downloader, with a
  pre-flight memory check that skips it entirely rather than risk a crash.
  Just as importantly, scraped jobs are now saved to disk *before* AI
  scoring runs instead of after, so a scoring failure can never again wipe
  out an entire scrape's results. See **Troubleshooting** for details.
- **Resilient Chrome driver startup** — Selenium 4's bundled "Selenium
  Manager" (a Rust binary that auto-detects Chrome and downloads a matching
  chromedriver) is known to crash on some Windows setups — typically with
  a `NoSuchDriverException` or a `memory allocation ... failed` error,
  usually caused by antivirus interference or a corrupted download of that
  binary. Both the auto-apply bot and the Naukri/Indeed/Glassdoor Selenium
  browser-fallback scrapers now try several driver resolution methods in
  order — an explicit `CHROMEDRIVER_PATH` override, `webdriver-manager`,
  `undetected-chromedriver`, then plain Selenium Manager as a last resort —
  and only fail with a clear, actionable message if every one of them fails.
  See **Troubleshooting** below if you hit this.

## Project overview

This project provides a dashboard for:
- scraping jobs from Naukri, LinkedIn, Indeed, and Glassdoor
- scoring jobs using local AI-based matching
- tracking application status, interview dates, and notes
- auto-applying to jobs on supported portals using your saved profile answers
- generating cover letters, interview prep, and skill fit analysis
- generating outreach copy (LinkedIn notes, InMail, cold email, follow-ups)
- exporting job data to Excel, filtered however you like

## Key capabilities

- Local Flask web application (`run.py`)
- SQLite database for job tracking (`data/jobs/jobs.db`)
- JSON storage for scraped job data (`data/jobs/jobs_data.json`)
- AI scoring using SentenceTransformer embeddings (100% local)
- Selenium-based auto-apply bot (`Apply/Apply-2/app.py`) driven by a
  spreadsheet-backed profile (`Apply/Apply-2/details.xlsx`), now editable
  from the web UI's Applicant Profile page
- LinkedIn browser session scraping support via Playwright
- **Outreach Agent**: generates and tracks personalised networking messages
  (LinkedIn connection notes, InMail/DM, cold emails, and follow-ups) for
  recruiters and hiring managers, going beyond just applying to a listing

## Project pipeline

1. User starts a job search from the **Scrape Portals** tab.
2. Scraper orchestration loads current jobs from `data/jobs/jobs_data.json`.
3. Selected platforms scrape new listings and merge them by `Job ID`.
4. New and updated jobs are appended to the JSON store.
5. The AI scoring pipeline checks jobs with missing or zero `AI Score`.
6. `SentenceTransformer` computes a local similarity score, saved as
   `AI Score` and `Match Score`.
7. Updated jobs are persisted back to `data/jobs/jobs_data.json`.
8. Jobs are synced into SQLite via `app.services.job_service.upsert_job`.
9. The UI loads merged JSON+DB job data into the Jobs table and Dashboard.
10. From **Auto-Apply**, a required-fields readiness check compares each job
    against your Applicant Profile before you run the bot.

## Required-fields auto-apply

1. Fill in your answers once on the **Applicant Profile** tab — identity,
   location, compensation, experience, skills, links, EEO/compliance, etc.
   Anything left blank shows up as "missing" everywhere else in the app.
2. Open a job in the drawer and check its **Required Fields** tab, or go to
   **Auto-Apply → "Scan New jobs for required fields"** to check them all
   at once.
3. Jobs the bot can fully answer are marked **Ready**; jobs missing an
   answer list exactly which fields are missing so you can fill them in.
4. Click **Start auto-apply** (all "New" jobs), select specific rows on the
   Jobs tab and use **Auto-Apply selected**, or paste a list of Job IDs/URLs
   into **"Apply to specific Job IDs"** on the Auto-Apply tab and run just
   that list — the bot fills each portal's form fields from your profile
   and logs progress live.

This detection is a heuristic based on the job's title/description text and
a shared field catalog (`app/config/required_fields.py`) — it tells you
what's *likely* to be asked, not a guarantee of the exact form on every
portal.

## External-portal detection ("redirect and skip")

Naukri, LinkedIn, Indeed, and Glassdoor frequently redirect an "Apply" click
to an external ATS (Workday, Lever, Greenhouse, SuccessFactors, Taleo,
Oracle, iCIMS, SmartRecruiters, Jobvite, BambooHR, JobDiva, Workable, and
others) or straight to a company's own career site. Blindly attempting to
drive every one of those unfamiliar forms is unreliable, so by default the
bot instead:

1. Detects the moment the URL/domain changes after clicking Apply.
2. Captures the company, title, source platform, original URL, redirect
   URL, detected ATS name, and a timestamp.
3. Marks the job **"External Portal"** and moves straight to the next job —
   no guessing at fields it doesn't recognize.
4. Logs a structured event to `data/logs/apply_events.jsonl` for later
   review, in addition to the human-readable console log.

Every job marked this way shows up in the **External Portals — needs
manual completion** panel on the Auto-Apply tab, with the detected ATS name
and a direct link to finish the application yourself.

If you'd rather restore the old best-effort behaviour (attempt a generic/
ATS-specific auto-fill on the redirected page before giving up), set the
environment variable `EXTERNAL_PORTAL_MODE=attempt` before running the bot.

## Outreach Agent (networking)

Applying is only half the job search — the Outreach Agent helps you reach
the human on the other side of the listing:

1. Open a job in the drawer (or use the standalone generator on the
   **AI Copilot** tab, or the **"Generate outreach by Job ID"** box at the
   top of the **Outreach** tab — paste a Job ID or URL and generate the
   full pack without opening the Jobs tab).
2. Optionally add the recruiter/hiring manager's name for a more personal
   touch.
3. Click **Generate outreach pack** to get:
   - A LinkedIn connection request note (kept under LinkedIn's 300-character
     limit)
   - A longer LinkedIn InMail/DM pitch
   - A cold outreach email (subject + body)
   - A polite follow-up message for when there's no reply
4. Track the channel used, recruiter contact info, outreach status, sent
   date, and follow-up date directly on the job record.
5. The **Outreach queue** on the Outreach tab surfaces your best-matching
   jobs that you haven't fully followed up on yet, so nothing falls through
   the cracks.

All outreach and resume/cover-letter copy is generated locally from clean,
editable templates filled in with the job/candidate data — no external AI
service, no setup required.

## Project structure

```
job_agent/
├── run.py                          # Flask app and API routes
├── init_db.py                      # Database initialization
├── login_linkedin.py                # Save LinkedIn browser session
├── requirements.txt                 # Python requirements
├── app/
│   ├── ai/scorer.py                 # AI scoring + local text generation
│   ├── automation/browser.py        # Playwright browser helper
│   ├── config/required_fields.py    # Shared auto-apply field catalog
│   ├── database/db.py               # SQLite database connection
│   ├── models/job_model.py          # Job table schema
│   ├── outreach/outreach_service.py # Outreach Agent
│   ├── scrapers/                    # Platform scrapers and orchestrator
│   ├── services/job_service.py
│   ├── services/profile_service.py  # Applicant profile read/write
│   └── utils/excel_export.py        # Filtered Excel export
├── Apply/Apply-2/app.py             # Selenium auto-apply bot
├── Apply/Apply-2/details.xlsx       # Auto-apply profile answers (editable via UI)
├── frontend/templates/index.html    # Personal Job Portal dashboard (UI)
└── data/
    ├── jobs/jobs_data.json          # Scraped jobs data
    ├── jobs/jobs.db                 # SQLite tracking database
    └── exports/                     # Excel export output
```

## Setup

1. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

2. Install Playwright browsers for LinkedIn scraping:

```bash
playwright install chromium
```

3. Initialize the database:

```bash
python init_db.py
```

4. Start the app:

```bash
python run.py
```

5. Open the UI:

`http://127.0.0.1:5000`

## Optional features

### LinkedIn browser scraping

Run once to save a browser session:

```bash
python login_linkedin.py
```

Then enable the "LinkedIn (browser session)" platform option on the
**Scrape Portals** tab.

### Auto-apply bot

The auto-apply bot in `Apply/Apply-2/app.py` uses Selenium to fill and
submit portal application forms using the answers stored in
`Apply/Apply-2/details.xlsx`. Edit those answers from the **Applicant
Profile** tab in the UI instead of opening the spreadsheet directly.

## Customization

### Edit your candidate profile (display name shown in the UI)

Update the `CANDIDATE` dictionary in `run.py`:

```python
CANDIDATE = {
    "name": "Your Name",
    "experience": "3+ Years Experience",
    "location": "Bangalore, Karnataka",
    "email": "you@email.com",
    "phone": "+91 XXXXXXXXXX",
    "skills": ["SQL", "Python", "Power BI", "Excel", "Pandas", "ETL"],
}
```

### Edit your auto-apply answers

Use the **Applicant Profile** tab in the UI — it reads and writes
`Apply/Apply-2/details.xlsx` directly, grouped into Identity, Location,
Compensation, Experience, Skills, Links, Documents, and EEO/Compliance.

### Improve AI personalization

Edit profile and resume context in `app/ai/scorer.py` to make generated
text more tailored — all of it runs locally, no API key needed.

## Data storage

- `data/jobs/jobs_data.json`: source of scraped jobs and most recent job
  fields.
- `data/jobs/jobs.db`: SQLite database used for job tracking fields.
- `data/exports/`: directory for Excel report exports (filtered or full).
- `Apply/Apply-2/details.xlsx`: your auto-apply profile answers.

## Notes

- Jobs are scraped and stored locally; there is no cloud dependency for
  core matching, generation, or auto-apply.
- LinkedIn browser scraping requires a real login session and Playwright.
- The auto-apply bot requires Chrome + Selenium and, like any browser
  automation, may need occasional selector updates if a portal changes its
  form layout.
- If scraping is blocked or fails, retry from a standard home network.
