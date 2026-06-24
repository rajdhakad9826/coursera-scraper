#!/usr/bin/env python3
"""coursera-scraper CLI entry point."""

import argparse
import sys
import re

from browser_manager import BrowserManager, Browser

VERSION = "1.2.0"


def _slugify(text: str) -> str:
    return re.sub(r"\s+", "-", text.strip().lower())


def _resolve_url(course_input: str) -> str:
    if "coursera.org" in course_input:
        return course_input
    return f"https://www.coursera.org/learn/{_slugify(course_input)}"


def _no_browser_exit() -> None:
    print("\nNo supported browser found.")
    print("\nInstall one of:")
    for b in Browser:
        print(f"  - {b.value}")
    sys.exit(1)


def _resolve_browser(browser_arg: str | None) -> Browser:
    """Validate --browser arg or detect best available. Exits on error."""
    mgr = BrowserManager()
    if browser_arg:
        try:
            browser = Browser(browser_arg.lower())
        except ValueError:
            print(f"Error: unknown browser '{browser_arg}'.")
            print(f"Choose from: {', '.join(b.value for b in Browser)}")
            sys.exit(1)
        if browser not in mgr.detect_browsers():
            available = mgr.detect_browsers()
            print(f"Error: requested browser '{browser_arg}' not found.")
            if available:
                print("\nAvailable browsers:")
                for b in available:
                    print(f"  - {b.value}")
            else:
                _no_browser_exit()
            sys.exit(1)
        return browser
    else:
        browser = mgr.get_default_browser()
        if browser is None:
            _no_browser_exit()
        return browser


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_scrape(args: argparse.Namespace) -> None:
    from coursera_transcript_scraper import main as run_scraper

    browser = _resolve_browser(getattr(args, "browser", None))
    if not getattr(args, "browser", None):
        print(f"Detected browser: {browser.value}")

    if args.headless:
        print("Note: headless mode skips manual login prompt.")

    course_url = _resolve_url(args.course)
    run_scraper(course_url=course_url, browser=browser, headless=args.headless)


def cmd_login(args: argparse.Namespace) -> None:
    from session_manager import SessionManager

    browser = _resolve_browser(getattr(args, "browser", None))
    session = SessionManager(browser.value)

    print(f"\nBrowser : {browser.value}")
    print(f"Profile : {session.profile_dir}")
    print()

    mgr = BrowserManager()
    driver = mgr.create_driver(browser, headless=False, profile_dir=session.profile_dir)
    try:
        print("Opening Coursera login page...")
        driver.get("https://www.coursera.org/login")
        print("Log in using the browser window, then press ENTER here.")
        input("  > ")

        if session.is_logged_in(driver):
            email = session.detect_email(driver)
            session.save_session(driver, email=email)
            print(f"\nLogged in successfully.")
            if email:
                print(f"Email   : {email}")
            print(f"Profile : {session.profile_dir}")
            print("\nFuture scrape commands will skip login automatically.")
        else:
            print("\nWarning: login could not be verified. Session not saved.")
            print("Make sure you completed login before pressing ENTER.")
    finally:
        driver.quit()


def cmd_logout(args: argparse.Namespace) -> None:
    from session_manager import SessionManager

    browser_arg = getattr(args, "browser", None)
    if browser_arg:
        try:
            browser = Browser(browser_arg.lower())
        except ValueError:
            print(f"Unknown browser: {browser_arg}")
            sys.exit(1)
        session = SessionManager(browser.value)
        if session.profile_exists():
            session.logout()
            print(f"Logged out: {browser.value}")
        else:
            print(f"No active session for: {browser.value}")
    else:
        removed = SessionManager.logout_all()
        if removed:
            for b in removed:
                print(f"Logged out: {b}")
        else:
            print("No active sessions found.")


def cmd_status(_args: argparse.Namespace) -> None:
    from session_manager import SessionManager

    found = False
    for b in Browser:
        session = SessionManager(b.value)
        info = session.session_info()
        if info:
            found = True
            email     = info.get("email") or "not available"
            saved_at  = info.get("saved_at", "unknown")
            print(f"\nBrowser    : {b.value}")
            print(f"Logged in  : yes")
            print(f"Email      : {email}")
            print(f"Last login : {saved_at}")
            print(f"Profile    : {session.profile_dir}")

    if not found:
        print("\nNo saved sessions.")
        print("Run: coursera-scraper login")
    print()


def cmd_browsers(_args: argparse.Namespace) -> None:
    mgr = BrowserManager()
    available = set(mgr.detect_browsers())
    print("\nAvailable browsers:\n")
    for b in Browser:
        mark = "✓" if b in available else "✗"
        print(f"  {mark} {b.value}")
    print()
    if not available:
        _no_browser_exit()


def cmd_version(_args: argparse.Namespace) -> None:
    print(f"coursera-scraper {VERSION}")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="coursera-scraper",
        description="Scrape Coursera course transcripts to PDF.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    # scrape
    p_scrape = sub.add_parser("scrape", help="Scrape a course and generate PDF")
    p_scrape.add_argument("course", help="Course URL or name/slug (e.g. 'machine-learning')")
    p_scrape.add_argument("--browser", metavar="NAME", help="Browser: chrome, edge, chromium, firefox")
    p_scrape.add_argument("--headless", action="store_true", help="Run headless (skips manual login prompt)")
    p_scrape.set_defaults(func=cmd_scrape)

    # login
    p_login = sub.add_parser("login", help="Save login session for future scrapes")
    p_login.add_argument("--browser", metavar="NAME", help="Browser to use (default: auto-detected)")
    p_login.set_defaults(func=cmd_login)

    # logout
    p_logout = sub.add_parser("logout", help="Remove saved session")
    p_logout.add_argument("--browser", metavar="NAME", help="Remove specific browser session (default: all)")
    p_logout.set_defaults(func=cmd_logout)

    # status
    p_status = sub.add_parser("status", help="Show saved session info")
    p_status.set_defaults(func=cmd_status)

    # browsers
    p_browsers = sub.add_parser("browsers", help="List detected browsers")
    p_browsers.set_defaults(func=cmd_browsers)

    # version
    p_version = sub.add_parser("version", help="Show version")
    p_version.set_defaults(func=cmd_version)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
