from __future__ import annotations

import os
import sys
import shutil
from enum import Enum
from pathlib import Path
from typing import Optional

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.edge.options import Options as EdgeOptions


class Browser(Enum):
    CHROME = "chrome"
    CHROMIUM = "chromium"
    EDGE = "edge"
    FIREFOX = "firefox"


# Auto-selection priority order
PRIORITY: list[Browser] = [Browser.CHROME, Browser.EDGE, Browser.CHROMIUM, Browser.FIREFOX]

_COMMON_PATHS: dict[Browser, dict[str, list[str]]] = {
    Browser.CHROME: {
        "linux": [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/snap/bin/google-chrome",
            "/usr/local/bin/google-chrome",
        ],
        "darwin": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
        "win32": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ],
    },
    Browser.CHROMIUM: {
        "linux": [
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/snap/bin/chromium",
        ],
        "darwin": ["/Applications/Chromium.app/Contents/MacOS/Chromium"],
        "win32": [r"C:\Program Files\Chromium\Application\chrome.exe"],
    },
    Browser.EDGE: {
        "linux": [
            "/usr/bin/microsoft-edge",
            "/usr/bin/microsoft-edge-stable",
            "/opt/microsoft/msedge/msedge",
        ],
        "darwin": ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"],
        "win32": [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ],
    },
    Browser.FIREFOX: {
        "linux": [
            "/usr/bin/firefox",
            "/snap/bin/firefox",
            "/usr/lib/firefox/firefox",
        ],
        "darwin": ["/Applications/Firefox.app/Contents/MacOS/firefox"],
        "win32": [
            r"C:\Program Files\Mozilla Firefox\firefox.exe",
            r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
        ],
    },
}

_PATH_NAMES: dict[Browser, list[str]] = {
    Browser.CHROME:   ["google-chrome", "google-chrome-stable", "chrome"],
    Browser.CHROMIUM: ["chromium", "chromium-browser"],
    Browser.EDGE:     ["microsoft-edge", "microsoft-edge-stable", "msedge"],
    Browser.FIREFOX:  ["firefox"],
}


def _find_executable(browser: Browser) -> Optional[str]:
    platform = "win32" if sys.platform == "win32" else ("darwin" if sys.platform == "darwin" else "linux")

    for path in _COMMON_PATHS.get(browser, {}).get(platform, []):
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    for name in _PATH_NAMES.get(browser, []):
        found = shutil.which(name)
        if found:
            return found

    return None


class BrowserManager:
    def detect_browsers(self) -> list[Browser]:
        """Return all installed browsers."""
        return [b for b in Browser if _find_executable(b) is not None]

    def get_default_browser(self) -> Optional[Browser]:
        """Return highest-priority available browser."""
        available = self.detect_browsers()
        for b in PRIORITY:
            if b in available:
                return b
        return None

    def create_driver(
        self,
        browser: Browser,
        headless: bool = False,
        profile_dir: Optional[Path] = None,
        optimize: bool = False,
    ) -> webdriver.Remote:
        """Create a Selenium WebDriver. Uses Selenium Manager for automatic driver downloads.

        profile_dir: persistent profile path — Chrome/Edge auto-save cookies there.
        optimize: disable images/GPU/notifications for faster scraping.
        """
        if browser in (Browser.CHROME, Browser.CHROMIUM):
            opts = ChromeOptions()
            opts.page_load_strategy = "eager"
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            opts.add_argument("--disable-blink-features=AutomationControlled")
            opts.add_experimental_option("excludeSwitches", ["enable-automation"])
            opts.add_experimental_option("useAutomationExtension", False)
            opts.add_argument("--window-size=1280,900")
            opts.add_argument(
                "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
            if headless:
                opts.add_argument("--headless=new")
            if optimize or headless:
                opts.add_argument("--blink-settings=imagesEnabled=false")
                opts.add_argument("--disable-notifications")
                opts.add_argument("--disable-gpu")
                opts.add_argument("--log-level=3")
                opts.add_argument("--disable-extensions")
            if profile_dir:
                profile_dir.mkdir(parents=True, exist_ok=True)
                opts.add_argument(f"--user-data-dir={profile_dir}")
                opts.add_argument("--profile-directory=Default")
            if browser == Browser.CHROMIUM:
                exe = _find_executable(Browser.CHROMIUM)
                if exe:
                    opts.binary_location = exe
            driver = webdriver.Chrome(options=opts)

        elif browser == Browser.EDGE:
            opts = EdgeOptions()
            opts.page_load_strategy = "eager"
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            opts.add_argument("--disable-blink-features=AutomationControlled")
            opts.add_experimental_option("excludeSwitches", ["enable-automation"])
            opts.add_experimental_option("useAutomationExtension", False)
            opts.add_argument("--window-size=1280,900")
            if headless:
                opts.add_argument("--headless=new")
            if optimize or headless:
                opts.add_argument("--blink-settings=imagesEnabled=false")
                opts.add_argument("--disable-notifications")
                opts.add_argument("--disable-gpu")
                opts.add_argument("--log-level=3")
            if profile_dir:
                profile_dir.mkdir(parents=True, exist_ok=True)
                opts.add_argument(f"--user-data-dir={profile_dir}")
                opts.add_argument("--profile-directory=Default")
            driver = webdriver.Edge(options=opts)

        elif browser == Browser.FIREFOX:
            opts = FirefoxOptions()
            opts.page_load_strategy = "eager"
            if headless:
                opts.add_argument("--headless")
            if optimize or headless:
                opts.set_preference("permissions.default.image", 2)
                opts.set_preference("dom.webnotifications.enabled", False)
            if profile_dir:
                profile_dir.mkdir(parents=True, exist_ok=True)
                opts.add_argument("-profile")
                opts.add_argument(str(profile_dir))
            driver = webdriver.Firefox(options=opts)

        else:
            raise ValueError(f"Unsupported browser: {browser}")

        if browser in (Browser.CHROME, Browser.CHROMIUM, Browser.EDGE):
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
            )

        return driver
