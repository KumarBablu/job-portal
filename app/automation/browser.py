from pathlib import Path
from playwright.sync_api import sync_playwright

# Persistent profile keeps login sessions
PROFILE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "browser" / "profile"
PROFILE_PATH.mkdir(parents=True, exist_ok=True)


def get_chrome_driver(options=None):
    """Build a Selenium Chrome WebDriver, resilient to Selenium Manager
    crashes (a known flaky Windows issue — the bundled Rust binary that
    auto-detects Chrome/chromedriver sometimes fails with a "memory
    allocation failed" error, usually due to antivirus interference).

    Tries, in order: plain Selenium Manager, an explicit CHROMEDRIVER_PATH
    env var, webdriver-manager (downloads a matching driver itself,
    bypassing Selenium Manager), then undetected-chromedriver.
    Used by the Naukri/Indeed/Glassdoor Selenium browser-fallback scrapers.

    IMPORTANT: every attempt gets its own fresh copy of the caller's Chrome
    options. Reusing a single Options object across attempts is unsafe —
    undetected-chromedriver mutates whatever Options object it's given,
    which would silently corrupt a shared object and break a later attempt
    with confusing errors like "unrecognized chrome option: excludeSwitches".
    """
    import os
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    base = options  # caller-supplied template options (arguments only)

    def cloned_options(minimal: bool = False) -> Options:
        fresh = Options()
        if base is not None:
            for arg in list(getattr(base, "arguments", [])):
                fresh.add_argument(arg)
            if not minimal:
                for key, val in dict(getattr(base, "experimental_options", {}) or {}).items():
                    try:
                        fresh.add_experimental_option(key, val)
                    except Exception:
                        pass
        # Memory-frugal flags — reduce Chrome's own memory footprint, which
        # matters on machines where Windows' pagefile is too small to grant
        # Chrome all the memory it would normally use.
        for flag in ("--disable-gpu", "--disable-dev-shm-usage", "--disable-extensions",
                     "--disable-background-networking", "--disable-default-apps",
                     "--disable-sync", "--metrics-recording-only", "--mute-audio",
                     "--no-first-run", "--disable-backgrounding-occluded-windows",
                     "--disable-renderer-backgrounding"):
            if flag not in fresh.arguments:
                fresh.add_argument(flag)
        return fresh

    errors = []

    try:
        return webdriver.Chrome(options=cloned_options())
    except Exception as e:
        errors.append(f"Selenium Manager (default): {e}")

    chromedriver_path = os.environ.get("CHROMEDRIVER_PATH", "").strip()
    if chromedriver_path:
        try:
            return webdriver.Chrome(service=Service(executable_path=chromedriver_path), options=cloned_options())
        except Exception as e:
            errors.append(f"CHROMEDRIVER_PATH: {e}")

    try:
        from webdriver_manager.chrome import ChromeDriverManager
        return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=cloned_options())
    except Exception as e:
        errors.append(f"webdriver-manager: {e}")

    try:
        import undetected_chromedriver as uc
        # minimal=True: undetected-chromedriver's patched driver rejects
        # excludeSwitches/useAutomationExtension on some newer Chrome
        # versions ("unrecognized chrome option: excludeSwitches") — and it
        # doesn't need them anyway, since it does its own anti-detection
        # patching.
        return uc.Chrome(options=cloned_options(minimal=True))
    except Exception as e:
        errors.append(f"undetected-chromedriver: {e}")

    raise RuntimeError(
        "Could not start Chrome via any driver resolution method:\n" +
        "\n".join(f"  - {e}" for e in errors) +
        "\n\nTry: pip install --upgrade webdriver-manager undetected-chromedriver, "
        "temporarily disable antivirus real-time protection, or set "
        "CHROMEDRIVER_PATH to a manually downloaded chromedriver "
        "(https://googlechromelabs.github.io/chrome-for-testing/). "
        "If you see 'tab crashed', 'paging file is too small', or other "
        "memory-allocation errors, increase your Windows virtual memory "
        "(pagefile) size in Settings > System > About > Advanced system "
        "settings > Performance > Settings > Advanced > Virtual memory."
    )


def start_browser(headless: bool = False):
    """
    Returns (playwright, browser_context, page).
    Uses a persistent context so LinkedIn / Naukri sessions survive.
    """
    playwright = sync_playwright().start()

    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_PATH),
        headless=headless,
        args=[
            "--start-maximized",
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ],
        viewport={"width": 1400, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
    )

    page = context.new_page()
    return playwright, context, page
