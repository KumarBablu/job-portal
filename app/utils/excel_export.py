"""
Export jobs_data.json to a formatted Excel workbook.
"""
import json
from pathlib import Path
from datetime import datetime

try:
    import pandas as pd
    from openpyxl import load_workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    HAS_EXCEL = True
except ImportError:
    HAS_EXCEL = False

JOBS_FILE   = Path(__file__).resolve().parent.parent.parent / "data" / "jobs" / "jobs_data.json"
EXPORT_DIR  = Path(__file__).resolve().parent.parent.parent / "data" / "exports"
EXPORT_FILE = EXPORT_DIR / "Job_Leads_Export.xlsx"

COLUMNS = [
    "Job ID", "Platform", "Keyword", "Title", "Company", "Location",
    "Salary", "Experience", "URL", "Scraped At",
    "Job Description", "Key Skills", "Job Highlights", "Role", "Industry Type", "Department", "Education",
    "AI Score", "AI Reason",
    "Status", "Applied Date", "Contact", "Interview Date",
    "Follow-Up", "Result", "Cover Letter", "Notes",
]


def _job_matches_filters(job: dict, filters: dict) -> bool:
    """Apply the same filter semantics as the Jobs table in the UI."""
    if not filters:
        return True

    status = filters.get("status")
    if status and str(job.get("Status", "New")).lower() != status.lower():
        return False

    platform = filters.get("platform")
    if platform and str(job.get("Platform", "")).lower() != platform.lower():
        return False

    outreach_status = filters.get("outreach_status")
    if outreach_status and str(job.get("Outreach Status", "")).lower() != outreach_status.lower():
        return False

    min_score = filters.get("min_score")
    if min_score not in (None, ""):
        try:
            if float(job.get("AI Score") or 0) < float(min_score):
                return False
        except (TypeError, ValueError):
            pass

    max_score = filters.get("max_score")
    if max_score not in (None, ""):
        try:
            if float(job.get("AI Score") or 0) > float(max_score):
                return False
        except (TypeError, ValueError):
            pass

    search = filters.get("search")
    if search:
        haystack = " ".join(str(job.get(k, "")) for k in
                             ("Title", "Company", "Location", "Key Skills", "Keyword")).lower()
        if search.lower() not in haystack:
            return False

    location = filters.get("location")
    if location and location.lower() not in str(job.get("Location", "")).lower():
        return False

    ids = filters.get("ids")
    if ids:
        wanted = {str(i).strip().lower() for i in ids if i is not None}
        job_key = str(job.get("Job ID") or job.get("id") or "").strip().lower()
        job_url = str(job.get("URL", "")).strip().lower()
        if wanted and job_key not in wanted and job_url not in wanted:
            return False

    scraped = str(job.get("Scraped At", ""))[:10]
    date_from = filters.get("date_from")
    if date_from and scraped and scraped < date_from:
        return False
    date_to = filters.get("date_to")
    if date_to and scraped and scraped > date_to:
        return False

    return True


def export_to_excel(filters: dict = None) -> Path:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    if not HAS_EXCEL:
        raise RuntimeError("pandas/openpyxl not installed")

    # Load jobs
    if not JOBS_FILE.exists():
        raise FileNotFoundError("No jobs_data.json found. Run a search first.")

    jobs_dict = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    if not jobs_dict:
        raise ValueError("No jobs to export.")

    rows = []
    for jid, job in jobs_dict.items():
        if not _job_matches_filters(job, filters):
            continue
        row = {col: job.get(col, "") for col in COLUMNS}
        rows.append(row)

    if not rows:
        raise ValueError("No jobs match the selected filters.")

    df = pd.DataFrame(rows, columns=COLUMNS)
    df = df.sort_values("AI Score", ascending=False)

    # Write with openpyxl for formatting
    df.to_excel(EXPORT_FILE, index=False, engine="openpyxl")

    # Format the workbook
    try:
        wb = load_workbook(EXPORT_FILE)
        ws = wb.active
        ws.title = "Job Leads"

        # Header style
        header_fill = PatternFill("solid", fgColor="0D1B2A")
        header_font = Font(bold=True, color="00D4FF", size=11)
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")

        # Column widths
        col_widths = {
            "Job ID": 16, "Platform": 12, "Keyword": 18, "Title": 32,
            "Company": 24, "Location": 22, "Salary": 14, "Experience": 14,
            "Job Description": 60, "Key Skills": 30, "Job Highlights": 30, "Role": 18,
            "Industry Type": 18, "Department": 18, "Education": 18,
            "URL": 50, "Scraped At": 16, "AI Score": 10, "AI Reason": 20,
            "Status": 14, "Applied Date": 14, "Contact": 18,
            "Interview Date": 16, "Follow-Up": 14, "Result": 12,
            "Cover Letter": 40, "Notes": 30,
        }
        for i, col in enumerate(COLUMNS, 1):
            ws.column_dimensions[get_column_letter(i)].width = col_widths.get(col, 15)

        # Colour rows by score
        green_fill  = PatternFill("solid", fgColor="1E4D36")
        yellow_fill = PatternFill("solid", fgColor="4A3800")

        for row in ws.iter_rows(min_row=2):
            try:
                score = float(row[COLUMNS.index("AI Score")].value or 0)
                if score >= 65:
                    for cell in row:
                        cell.fill = green_fill
                elif score >= 45:
                    for cell in row:
                        cell.fill = yellow_fill
            except Exception:
                pass

        # Freeze header row
        ws.freeze_panes = "A2"

        wb.save(EXPORT_FILE)
    except Exception:
        pass  # formatting failed, plain export is still fine

    return EXPORT_FILE
