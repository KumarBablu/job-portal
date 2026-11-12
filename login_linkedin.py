"""
Run this ONCE to save your LinkedIn login session.
After running, the browser scraper can use the saved session.

Usage:
    python login_linkedin.py

Steps:
    1. A browser window opens at LinkedIn login page
    2. Log in manually with your email + password
    3. Complete any 2FA / CAPTCHA if prompted
    4. Press ENTER in this terminal once you're logged in
    5. Session is saved — close the browser
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.automation.browser import start_browser

print()
print("=" * 55)
print("  LinkedIn Session Login")
print("=" * 55)
print()
print("  A browser window will open.")
print("  Log in to LinkedIn manually.")
print("  Press ENTER here after you are logged in.")
print()

playwright, browser, page = start_browser(headless=False)

page.goto("https://www.linkedin.com/login")

print("  Browser opened. Please log in…")
input("  Press ENTER when logged in: ")

print()
print("  ✅ Session saved to data/browser/profile/")
print("  You can now use LinkedIn (browser) mode in the Job Agent.")
print()

browser.close()
playwright.stop()
