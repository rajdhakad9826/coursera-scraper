"""Persistent login session management for coursera-scraper."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException

COURSERA_HOME = "https://www.coursera.org"
_MY_LEARNING  = "https://www.coursera.org/my-learning"


class SessionManager:
    BASE_DIR = Path.home() / ".coursera-scraper" / "profiles"

    def __init__(self, browser_value: str) -> None:
        self.browser_value = browser_value
        self.profile_dir   = self.BASE_DIR / browser_value
        self.cookies_path  = self.profile_dir / "cookies.json"
        self._info_path    = self.profile_dir / "session_info.json"

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def profile_exists(self) -> bool:
        return self.profile_dir.exists()

    def session_info(self) -> dict:
        """Return saved metadata, or {} if none."""
        try:
            return json.loads(self._info_path.read_text())
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # Auth check — navigates to my-learning, checks URL
    # ------------------------------------------------------------------

    def is_logged_in(self, driver: webdriver.Remote) -> bool:
        try:
            driver.get(_MY_LEARNING)
            WebDriverWait(driver, 10).until(
                lambda d: d.execute_script("return document.readyState") in ("complete", "interactive")
            )
            return "login" not in driver.current_url
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Persist / restore
    # ------------------------------------------------------------------

    def save_session(self, driver: webdriver.Remote, email: str = "") -> None:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        # Cookie backup (used by Firefox; Chrome persists via --user-data-dir)
        cookies = driver.get_cookies()
        self.cookies_path.write_text(json.dumps(cookies, indent=2))
        # Metadata
        self._info_path.write_text(json.dumps({
            "browser":     self.browser_value,
            "saved_at":    datetime.now().isoformat(),
            "profile_dir": str(self.profile_dir),
            "email":       email,
        }, indent=2))

    def load_cookies(self, driver: webdriver.Remote) -> bool:
        """Inject saved cookies into driver. Returns True if cookies were loaded."""
        if not self.cookies_path.exists():
            return False
        try:
            driver.get(COURSERA_HOME)
            WebDriverWait(driver, 8).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            cookies = json.loads(self.cookies_path.read_text())
            for cookie in cookies:
                cookie.pop("sameSite", None)  # avoid cross-origin rejection
                try:
                    driver.add_cookie(cookie)
                except Exception:
                    pass
            driver.refresh()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Logout
    # ------------------------------------------------------------------

    def logout(self) -> None:
        if self.profile_dir.exists():
            shutil.rmtree(self.profile_dir)

    @classmethod
    def logout_all(cls) -> list[str]:
        """Remove all browser profiles. Returns list of removed browser names."""
        removed: list[str] = []
        if cls.BASE_DIR.exists():
            for p in cls.BASE_DIR.iterdir():
                if p.is_dir():
                    removed.append(p.name)
                    shutil.rmtree(p)
        return removed

    # ------------------------------------------------------------------
    # Email detection (best-effort, used during login command)
    # ------------------------------------------------------------------

    def detect_email(self, driver: webdriver.Remote) -> str:
        """Try to extract the logged-in email. Returns empty string on failure."""
        try:
            driver.get("https://www.coursera.org/account-profile")
            WebDriverWait(driver, 8).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            for selector in (
                "[data-testid*='email']",
                "[class*='email']",
                "input[type='email']",
            ):
                try:
                    el = driver.find_element(By.CSS_SELECTOR, selector)
                    val = el.get_attribute("value") or el.text
                    if val and "@" in val:
                        return val.strip()
                except Exception:
                    continue
        except Exception:
            pass
        return ""
