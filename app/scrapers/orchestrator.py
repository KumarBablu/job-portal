"""
Scraper orchestrator — runs all scrapers in a background thread,
writes results to jobs_data.json and the SQLite DB.
"""
import json
import threading
from itertools import product
from pathlib import Path
from datetime import datetime

_state = {"running": False, "log": []}

JOBS_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "jobs" / "jobs_data.json"
JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)


def get_state():
    return _state


def _ts():
    return datetime.now().strftime("%H:%M:%S")


def _log(msg):
    _state["log"].append({"ts": _ts(), "msg": msg})
    print(f"[{_ts()}] {msg}", flush=True)


def load_jobs_file():
    if JOBS_FILE.exists():
        try:
            return json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _scrape_platform_task(keyword, location, platform, max_jobs, log_fn, freshness_days=0):
    try:
        if platform == "naukri":
            from app.scrapers.naukri_scraper import scrape_naukri_api
            jobs = scrape_naukri_api(keyword, location, max_jobs, log_fn)

        elif platform == "linkedin":
            from app.scrapers.linkedin_scraper import scrape_linkedin_public, scrape_linkedin_with_browser
            jobs = scrape_linkedin_public(keyword, location, max_jobs, log_fn, freshness_days=freshness_days)
            if not jobs:
                log_fn("  LinkedIn public scrape returned 0 jobs; falling back to browser mode")
                jobs = scrape_linkedin_with_browser(keyword, location, max_jobs, log_fn, freshness_days=freshness_days)

        elif platform == "linkedin-browser":
            from app.scrapers.linkedin_scraper import scrape_linkedin_with_browser
            jobs = scrape_linkedin_with_browser(keyword, location, max_jobs, log_fn, freshness_days=freshness_days)

        elif platform == "indeed":
            from app.scrapers.indeed_scraper import scrape_indeed
            jobs = scrape_indeed(keyword, location, max_jobs, log_fn)

        elif platform == "glassdoor":
            from app.scrapers.glassdoor_scraper import scrape_glassdoor
            jobs = scrape_glassdoor(keyword, location, max_jobs, log_fn)

        elif platform in ("open-jobs", "openjobs", "remote"):
            from app.scrapers.open_jobs_scraper import scrape_open_jobs
            jobs = scrape_open_jobs(keyword, location, max_jobs, log_fn)

        else:
            jobs = []

        return platform, keyword, location, jobs

    except Exception as e:
        log_fn(f"❌ Error [{platform}/{keyword}/{location}]: {e}")
        return platform, keyword, location, []


def save_jobs_file(jobs):
    JOBS_FILE.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")


def run_scrape_job(config):
    _state["running"] = True
    _state["log"] = []

    try:
        keywords  = [k.strip() for k in config.get("keywords", "").splitlines() if k.strip()]
        companies = [c.strip() for c in config.get("companies", "").splitlines() if c.strip()]
        locations = [l.strip() for l in config.get("locations", "").splitlines() if l.strip()]
        platforms = config.get("platforms", [])
        use_ai    = config.get("use_ai", True)
        max_jobs  = int(config.get("max_jobs", 25))
        freshness = int(config.get("freshness") or 0)

        if not locations:
            locations = [""]

        if companies and keywords:
            search_terms = [f"{k} {c}" for k in keywords for c in companies]
        elif companies:
            search_terms = companies
        else:
            search_terms = keywords

        _log(f"🚀 Job search started")
        _log(f"   Keywords ({len(keywords)}): {', '.join(keywords[:5])}{'…' if len(keywords)>5 else ''}")
        _log(f"   Companies ({len(companies)}): {', '.join(companies[:5])}{'…' if len(companies)>5 else ''}")
        _log(f"   Locations: {', '.join(locations) if locations and locations != [''] else 'Any'}")
        _log(f"   Freshness: {freshness or 'Any age'}")
        _log(f"   Platforms: {', '.join(platforms)}")
        _log(f"   Max/search: {max_jobs}")
        _log("─" * 48)

        all_jobs  = load_jobs_file()
        before    = len(all_jobs)
        new_count = 0
        updated_count = 0

        task_pairs = list(product(search_terms, locations))
        total_tasks = len(task_pairs) * len(platforms)
        _log(f"🧩 Starting {total_tasks} scraper tasks sequentially")

        if task_pairs and platforms:
            completed = 0
            completed_by_platform = {}

            for platform in platforms:
                _log(f"🔎 Starting platform: {platform}")
                for keyword, location in task_pairs:
                    platform_result, keyword_result, location_result, found = _scrape_platform_task(
                        keyword, location, platform, max_jobs, _log, freshness_days=freshness
                    )

                    if companies:
                        company_filters = [c.lower() for c in companies]
                        found = [job for job in found if any(cf in ((job.get("Company", "") + " " + job.get("Listing", "")).lower()) for cf in company_filters)]

                    for job in found:
                        jid = job.get("Job ID", "")
                        if not jid:
                            continue

                        existing = all_jobs.get(jid)
                        if existing:
                            updated = False
                            for key, value in job.items():
                                if value and existing.get(key) != value:
                                    existing[key] = value
                                    updated = True
                            if updated:
                                all_jobs[jid] = existing
                                updated_count += 1
                        else:
                            all_jobs[jid] = job
                            new_count += 1

                    completed += 1
                    completed_by_platform[platform_result] = completed_by_platform.get(platform_result, 0) + 1
                    _log(f"  ✅ Completed {platform_result} | {keyword_result} | {location_result} — jobs: {len(found)}")

            try:
                summary = ", ".join([f"{p}:{c}" for p, c in completed_by_platform.items()]) or "none"
                _log(f"🧾 Tasks completed: {completed}/{total_tasks} — by platform: {summary}")
            except Exception:
                pass
        else:
            _log("⚠ No platforms, keywords, or locations provided for scraping")

        # Cloud Fallback: If 0 jobs found (e.g. cloud datacenter blocked by Naukri/Indeed/Glassdoor),
        # query open APIs automatically so the mobile scan never returns empty.
        if (new_count == 0 and updated_count == 0) and "open-jobs" not in platforms:
            _log("ℹ Cloud fallback: Selected platforms returned 0 jobs (typical on datacenter/cloud IPs). Automatically querying Open Job Feeds (Jobicy & Arbeitnow)...")
            try:
                from app.scrapers.open_jobs_scraper import scrape_open_jobs
                target_kws = keywords if keywords else ["Data Analyst"]
                for kw in target_kws[:3]:
                    fallback_jobs = scrape_open_jobs(kw, locations[0] if locations else "Remote", max_jobs=max_jobs, log_fn=_log)
                    for job in fallback_jobs:
                        jid = job.get("Job ID", "")
                        if not jid:
                            continue
                        if jid not in all_jobs:
                            all_jobs[jid] = job
                            new_count += 1
                        else:
                            updated_count += 1
            except Exception as fe:
                _log(f"  Fallback notice: {fe}")

        _log("─" * 48)
        _log(f"📦 New jobs found: {new_count}  |  Updated existing jobs: {updated_count} | Total in database: {len(all_jobs)}")

        # Save the scraped results FIRST, before attempting AI scoring — this
        # is deliberate: AI scoring can involve downloading a model on first
        # run, which has been observed to crash on some low-memory Windows
        # machines. Saving now means that even in the worst case (scoring
        # crashes the whole process), the actual scrape results are never
        # lost — only the AI scores would be missing, and a later scrape or
        # a server restart will pick up scoring the unscored jobs next time.
        save_jobs_file(all_jobs)
        _log("💾 Saved jobs_data.json")

        _log("🗄 Syncing to database…")
        try:
            _sync_to_db(all_jobs)
            _log("  ✅ Database updated")
        except Exception as e:
            _log(f"  ⚠ DB sync error: {e}")

        # AI scoring — best-effort, runs after the save above so its results
        # only ever add value and can never cause data loss if it fails.
        if use_ai:
            _log("🧠 Running AI match scoring…")
            try:
                from app.ai.scorer import score_jobs_batch

                def needs_scoring(job):
                    score = job.get("AI Score")
                    if score is None or score == "":
                        return True
                    try:
                        return float(score) == 0
                    except Exception:
                        return True

                to_score = [j for j in all_jobs.values() if needs_scoring(j)]
                if to_score:
                    scored = score_jobs_batch(to_score, _log)
                    for j in scored:
                        all_jobs[j["Job ID"]] = j
                    _log(f"  ✅ Scored {len(scored)} jobs")

                    save_jobs_file(all_jobs)
                    _log("💾 Saved jobs_data.json (with AI scores)")
                    try:
                        _sync_to_db(all_jobs)
                        _log("🎯 AI scoring results synced to DB")
                    except Exception as e:
                        _log(f"  ⚠ DB sync error: {e}")
                else:
                    _log("  All jobs already scored")
            except Exception as e:
                _log(f"  ⚠ AI scoring error: {e} — scrape results are already saved, scoring can be retried later")
            finally:
                # Free the embedding model's memory now rather than leaving
                # it cached for the rest of the server process's life — on
                # memory-constrained machines this meaningfully helps
                # whatever memory-heavy step runs next (most commonly the
                # Chrome-based auto-apply bot).
                try:
                    from app.ai.scorer import release_model
                    release_model()
                except Exception:
                    pass
        else:
            _log("⚡ AI scoring skipped (uncheck to enable in options)")

        _log(f"🎉 Done! {new_count} new jobs added. Click 'View Jobs →' to browse.")

    except Exception as e:
        import traceback
        _log(f"❌ Fatal error: {e}")
        _log(traceback.format_exc())
    finally:
        _state["running"] = False


def _sync_to_db(jobs_dict):
    from app.database.db import SessionLocal
    from app.services.job_service import upsert_jobs_batch
    db = SessionLocal()
    try:
        upsert_jobs_batch(db, jobs_dict)
    finally:
        db.close()


def start_scrape_thread(config):
    if _state["running"]:
        return False, "Scraper already running — please wait"
    t = threading.Thread(target=run_scrape_job, args=(config,), daemon=True)
    t.start()
    return True, "Started"
