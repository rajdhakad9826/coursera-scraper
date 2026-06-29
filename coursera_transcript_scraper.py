"""
Coursera Transcript Scraper & PDF Generator
============================================
Requirements:
    pip install selenium reportlab

Usage:
    python coursera_transcript_scraper.py           # interactive
    coursera-scraper scrape "machine-learning"      # CLI
"""

import logging
import os
import sys
import time
import re
import json
from datetime import datetime

# ==============================================================================
# Selenium Imports
# ==============================================================================
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# ==============================================================================
# ReportLab Imports
# ==============================================================================
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak, HRFlowable
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from browser_manager import BrowserManager, Browser


# ==============================================================================
# Configuration
# Edit these defaults or leave blank to be prompted
# ==============================================================================

DEFAULT_COURSE_URL = ""        # e.g. "https://www.coursera.org/learn/machine-learning"
OUTPUT_PDF = "coursera_transcripts.pdf"
CHECKPOINT_JSON = "coursera_transcripts_checkpoint.json"


# Seconds to wait for page elements before timing out
WAIT_TIMEOUT = 20


# ==============================================================================
# Browser Setup
# ==============================================================================

# ==============================================================================
# Login
# ==============================================================================

def login(driver: webdriver.Chrome, headless: bool = False, session_mgr=None) -> bool:
    print("\n[1/4] Checking authentication...")

    # Profile-based: Chrome/Edge auto-persist cookies in --user-data-dir
    if session_mgr and session_mgr.is_logged_in(driver):
        print("  [Saved session] Already logged in — skipping manual login.")
        return True

    # Cookie fallback (Firefox, or if profile cookies were lost)
    if session_mgr and session_mgr.cookies_path.exists():
        if session_mgr.load_cookies(driver) and session_mgr.is_logged_in(driver):
            print("  [Cookie restore] Session restored from cookies.")
            session_mgr.save_session(driver)
            return True

    # Session was previously established but is now expired → tell user, exit
    if session_mgr and session_mgr.session_info():
        print("\n[Error] Session expired.")
        print("Run: coursera-scraper login")
        sys.exit(1)

    # Manual login (first run or direct script usage)
    driver.get("https://www.coursera.org/login")
    if headless:
        print("  Headless mode: skipping manual login prompt.")
    else:
        print("  Please log in using the opened browser window.")
        input("  Press ENTER here in the terminal once you are logged in...")

    if session_mgr:
        session_mgr.save_session(driver)
        print("  Session saved — future runs will skip this step.")

    print("  [Success] Login complete.")
    return True


# ==============================================================================
# Course Navigation - Collect all lecture/item links
# ==============================================================================

def get_course_slug(url: str) -> str:
    m = re.search(r"coursera\.org/learn/([^/?#]+)", url)
    return m.group(1) if m else url.rstrip("/").split("/")[-1]


def _discover_module_count(driver: webdriver.Chrome, slug: str) -> int:
    """
    Navigate to the course home and discover how many modules exist.
    Tries multiple strategies to determine module count.
    """
    # Strategy 1: Visit course home and look for module/week navigation links
    home_url = f"https://www.coursera.org/learn/{slug}/home/welcome"
    driver.get(home_url)
    time.sleep(4)

    # Look for module navigation links in the sidebar/nav
    module_link_xpaths = [
        "//a[contains(@href,'/home/module/')]",
        "//a[contains(@href,'/home/week/')]",
        "//nav//a[contains(@href,'/home/')]",
    ]

    max_module = 0
    for xpath in module_link_xpaths:
        links = driver.find_elements(By.XPATH, xpath)
        for link in links:
            href = link.get_attribute("href") or ""
            m = re.search(r"/home/(?:module|week)/(\d+)", href)
            if m:
                num = int(m.group(1))
                if num > max_module:
                    max_module = num

    # Strategy 2: Look for numbered elements in the sidebar
    if max_module == 0:
        try:
            all_links = driver.find_elements(By.XPATH, "//a[@href]")
            for link in all_links:
                href = link.get_attribute("href") or ""
                m = re.search(r"/home/(?:module|week)/(\d+)", href)
                if m:
                    num = int(m.group(1))
                    if num > max_module:
                        max_module = num
        except Exception:
            pass

    # Strategy 3: If we still have 0, try probing module pages directly
    if max_module == 0:
        for n in range(1, 20):
            try:
                test_url = f"https://www.coursera.org/learn/{slug}/home/module/{n}"
                driver.get(test_url)
                time.sleep(2)
                # Check if we landed on a valid module page (not redirected to home)
                current = driver.current_url
                if f"/module/{n}" in current or f"/week/{n}" in current:
                    max_module = n
                else:
                    # If redirected away, we've gone past the last module
                    break
            except Exception:
                break

    return max_module


def _collect_links_on_page(driver: webdriver.Chrome, seen_urls: set, module_label: str) -> list[dict]:
    """
    Collect all lecture/item/supplement/quiz links visible on the current page.
    Returns list of dicts with week, title, url.
    """
    results = []

    # Broad set of XPath patterns for content links
    link_xpaths = [
        "//a[contains(@href,'/lecture/')]",
        "//a[contains(@href,'/item/')]",
        "//a[contains(@href,'/supplement/')]",
        "//a[contains(@href,'/quiz/')]",
        "//a[contains(@href,'/exam/')]",
        "//a[contains(@href,'/peer/')]",
        "//a[contains(@href,'/ungradedLab/')]",
        "//a[contains(@href,'/gradedLti/')]",
        "//a[contains(@href,'/discussionPrompt/')]",
    ]

    for xpath in link_xpaths:
        try:
            anchors = driver.find_elements(By.XPATH, xpath)
            for a in anchors:
                href = a.get_attribute("href") or ""
                if not href or href in seen_urls:
                    continue

                # Only include lecture-like content (skip quiz/peer/exam for transcripts)
                if not re.search(r"/(lecture|item|supplement|ungradedLab)/", href):
                    continue

                label = (a.text.strip()
                         or a.get_attribute("aria-label")
                         or a.get_attribute("title")
                         or "Untitled")

                # Clean up label - remove duration/time info if appended
                label = re.sub(r"\s*\d+\s*min(ute)?s?\s*$", "", label).strip()
                if not label:
                    label = "Untitled"

                if re.search(r"\breading\b|\bpractice\s+lab\b", label, re.IGNORECASE):
                    continue

                seen_urls.add(href)
                results.append({"week": module_label, "title": label, "url": href})
        except Exception:
            continue

    return results


def get_all_lecture_links(driver: webdriver.Chrome, course_url: str) -> list[dict]:
    """
    Returns a list of dicts: [{"week": str, "title": str, "url": str}, ...]
    Navigates through each module page to collect lecture links.
    """
    print("\n[2/4] Collecting lecture links...")
    slug = get_course_slug(course_url)

    lectures = []
    seen_urls = set()

    # Step 1: Discover how many modules exist
    print("  - Discovering modules...")
    module_count = _discover_module_count(driver, slug)
    print(f"  - Detected {module_count} module(s).")

    # Step 2: Visit each module page and collect links
    if module_count > 0:
        for mod_num in range(1, module_count + 1):
            module_label = f"Module {mod_num}"
            mod_url = f"https://www.coursera.org/learn/{slug}/home/module/{mod_num}"
            print(f"  - Scanning {module_label}...")
            driver.get(mod_url)
            time.sleep(3)

            # Expand collapsed sections within the module page
            _expand_all_sections(driver)
            time.sleep(1)

            # Try to get a better module title from the page heading
            try:
                heading_xpaths = [
                    "//h1",
                    "//h2[1]",
                    "//div[contains(@class,'module')]//h2",
                    "//div[contains(@class,'week')]//h2",
                ]
                for hx in heading_xpaths:
                    headings = driver.find_elements(By.XPATH, hx)
                    for h in headings:
                        txt = h.text.strip()
                        if txt and len(txt) < 120:
                            module_label = txt
                            break
                    if module_label != f"Module {mod_num}":
                        break
            except Exception:
                pass

            found = _collect_links_on_page(driver, seen_urls, module_label)
            lectures.extend(found)
            print(f"    Found {len(found)} item(s) in {module_label}.")

    # Step 3: Fallback - try the old /home/info (syllabus) page
    if not lectures:
        print("  - Trying syllabus page...")
        try:
            driver.get(f"https://www.coursera.org/learn/{slug}/home/info")
            time.sleep(3)
            _expand_all_sections(driver)
            time.sleep(1)
            found = _collect_links_on_page(driver, seen_urls, "Module")
            lectures.extend(found)
        except Exception:
            pass

    # Step 4: Fallback - scrape any /lecture/ or /item/ links on course page
    if not lectures:
        print("  - Trying course home page...")
        driver.get(course_url)
        time.sleep(3)
        anchors = driver.find_elements(By.XPATH, "//a[@href]")
        for a in anchors:
            href = a.get_attribute("href") or ""
            if re.search(r"/learn/[^/]+/(lecture|item|supplement)/", href) and href not in seen_urls:
                title = a.text.strip() or "Lecture"
                if re.search(r"\breading\b|\bpractice\s+lab\b", title, re.IGNORECASE):
                    continue
                seen_urls.add(href)
                lectures.append({
                    "week": "Module",
                    "title": title,
                    "url": href
                })

    # Step 5: Fallback - try /home/week/N if /home/module/N didn't work
    if not lectures and module_count == 0:
        print("  - Trying week-based navigation...")
        for n in range(1, 20):
            week_url = f"https://www.coursera.org/learn/{slug}/home/week/{n}"
            driver.get(week_url)
            time.sleep(3)
            current = driver.current_url
            if f"/week/{n}" not in current:
                break
            _expand_all_sections(driver)
            time.sleep(1)
            found = _collect_links_on_page(driver, seen_urls, f"Week {n}")
            lectures.extend(found)
            if not found:
                break

    print(f"  [Success] Found {len(lectures)} lecture(s).")
    return lectures


def _expand_all_sections(driver: webdriver.Chrome):
    """Click all expand/toggle buttons so hidden lecture links become visible."""
    expand_xpaths = [
        "//button[contains(@aria-label,'Expand')]",
        "//button[contains(@aria-label,'expand')]",
        "//button[contains(@aria-label,'Show')]",
        "//button[contains(@aria-label,'show')]",
        "//button[@aria-expanded='false']",
        "//button[contains(@class,'week-toggle') or contains(@class,'module-toggle')]",
        "//span[contains(text(),'Show all')]/parent::button",
        "//span[contains(text(),'show all')]/parent::button",
        "//button[contains(@class,'toggle') or contains(@class,'expand') or contains(@class,'collapse')]",
        "//div[contains(@class,'accordion')]//button[@aria-expanded='false']",
    ]
    for xpath in expand_xpaths:
        try:
            btns = driver.find_elements(By.XPATH, xpath)
            for btn in btns:
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                    time.sleep(0.05)
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(0.1)
                except Exception:
                    pass
        except Exception:
            pass


# ==============================================================================
# Transcript Extraction
# ==============================================================================

def extract_transcript(driver: webdriver.Chrome, lecture: dict) -> str:
    """Extract transcript. One-shot wrapper — prefer TranscriptExtractor for batch use."""
    from transcript_extractor import TranscriptExtractor
    ext = TranscriptExtractor(driver)
    ext.setup()
    return ext.extract(lecture)["transcript"]


# ==============================================================================
# PDF Generation
# ==============================================================================

def build_pdf(
    course_title: str,
    data: list[dict],          # [{"week": str, "title": str, "transcript": str}]
    output_path: str
):
    print("\n[4/4] Building PDF...")

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        rightMargin=2.5 * cm,
        leftMargin=2.5 * cm,
        topMargin=2.5 * cm,
        bottomMargin=2.5 * cm,
    )

    styles = getSampleStyleSheet()

    # Custom styles
    cover_title_style = ParagraphStyle(
        "CoverTitle",
        parent=styles["Title"],
        fontSize=26,
        leading=32,
        textColor=colors.HexColor("#1E3A5F"),
        alignment=TA_CENTER,
        spaceAfter=10,
    )
    cover_sub_style = ParagraphStyle(
        "CoverSub",
        parent=styles["Normal"],
        fontSize=12,
        textColor=colors.HexColor("#555555"),
        alignment=TA_CENTER,
        spaceAfter=6,
    )
    week_heading_style = ParagraphStyle(
        "WeekHeading",
        parent=styles["Heading1"],
        fontSize=16,
        leading=20,
        textColor=colors.HexColor("#1E3A5F"),
        spaceBefore=18,
        spaceAfter=6,
        borderPad=4,
    )
    lecture_title_style = ParagraphStyle(
        "LectureTitle",
        parent=styles["Heading2"],
        fontSize=13,
        leading=17,
        textColor=colors.HexColor("#2E6DA4"),
        spaceBefore=12,
        spaceAfter=4,
    )
    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontSize=10,
        leading=15,
        textColor=colors.HexColor("#222222"),
        spaceAfter=8,
        alignment=TA_LEFT,
    )
    note_label_style = ParagraphStyle(
        "NoteLabel",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.HexColor("#888888"),
        spaceAfter=2,
    )

    story = []

    # Cover page
    story.append(Spacer(1, 3 * cm))
    story.append(Paragraph("Coursera Course Transcripts", cover_title_style))
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph(course_title, cover_sub_style))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(
        f"Generated on {datetime.now().strftime('%B %d, %Y')}",
        cover_sub_style
    ))
    story.append(Spacer(1, 0.5 * cm))
    story.append(HRFlowable(width="80%", thickness=1.5, color=colors.HexColor("#1E3A5F"), hAlign="CENTER"))
    story.append(Spacer(1, 2 * cm))

    total = len(data)
    story.append(Paragraph(f"Total lectures: {total}", cover_sub_style))
    weeks = list(dict.fromkeys(d["week"] for d in data))
    story.append(Paragraph(f"Modules / Weeks: {len(weeks)}", cover_sub_style))
    story.append(PageBreak())

    # Transcripts
    current_week = None
    for item in data:
        if item["week"] != current_week:
            current_week = item["week"]
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#CCCCCC")))
            story.append(Paragraph(current_week, week_heading_style))
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#CCCCCC")))

        story.append(Paragraph(item["title"], lecture_title_style))

        # Notes placeholder
        story.append(Paragraph("[ Notes ]", note_label_style))
        story.append(HRFlowable(width="100%", thickness=0.3, color=colors.HexColor("#DDDDDD")))
        story.append(Spacer(1, 0.2 * cm))

        # Transcript body - escape XML special chars
        transcript_text = (
            item["transcript"]
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        # Split into paragraphs for nicer flow
        for para in transcript_text.split("\n\n"):
            para = para.strip()
            if para:
                story.append(Paragraph(para, body_style))

        story.append(Spacer(1, 0.4 * cm))

    doc.build(story)
    print(f"  [Success] PDF saved to: {os.path.abspath(output_path)}")


# ==============================================================================
# Main
# ==============================================================================

def main(
    course_url: str | None = None,
    browser: "Browser | None" = None,
    headless: bool = False,
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    print("=" * 60)
    print("  Coursera Transcript Scraper & PDF Generator")
    print("=" * 60)

    # Gather configuration (interactive fallback for direct script use)
    course_url = course_url or DEFAULT_COURSE_URL or input(
        "\nCourse URL (e.g. https://www.coursera.org/learn/machine-learning): "
    ).strip()

    if not re.search(r"coursera\.org/learn/", course_url):
        print("[Warning] That doesn't look like a Coursera course URL. Continuing anyway...")

    course_slug  = get_course_slug(course_url)
    course_title = course_slug.replace("-", " ").title()

    # Launch browser via BrowserManager
    mgr = BrowserManager()
    if browser is None:
        browser = mgr.get_default_browser()
        if browser is None:
            print("\n[Error] No supported browser found. Install Chrome, Edge, Chromium, or Firefox.")
            return
        print(f"  Using browser: {browser.value}")

    from session_manager import SessionManager
    from transcript_extractor import TranscriptExtractor

    session_mgr = SessionManager(browser.value)
    driver = mgr.create_driver(
        browser, headless=headless, profile_dir=session_mgr.profile_dir, optimize=True
    )
    extractor = TranscriptExtractor(driver)

    try:
        # Login
        login(driver, headless=headless, session_mgr=session_mgr)

        # Inject network interceptors after login, before lecture pages
        extractor.setup()

        # Collect lectures
        lectures = get_all_lecture_links(driver, course_url)
        if not lectures:
            print("\n[Error] No lectures found. Check the course URL and try again.")
            return

        # Load checkpoint if it exists (resume support)
        enriched = []
        scraped_urls = set()
        checkpoint_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), CHECKPOINT_JSON)
        if os.path.exists(checkpoint_path):
            try:
                with open(checkpoint_path, "r", encoding="utf-8") as f:
                    checkpoint_data = json.load(f)
                if checkpoint_data.get("course_slug") == course_slug:
                    enriched = checkpoint_data.get("lectures", [])
                    scraped_urls = {item.get("url", "") for item in enriched}
                    print(f"\n  [Info] Resuming from checkpoint - {len(enriched)} lecture(s) already scraped.")
            except Exception:
                pass

        remaining = [lec for lec in lectures if lec["url"] not in scraped_urls]
        total = len(lectures)
        done = total - len(remaining)
        print(f"\n[3/4] Scraping transcripts for {len(remaining)} remaining lecture(s) (of {total} total)...")

        # Attempt parallel API pre-fetch (fast path — no Selenium, no page loads)
        api_results: list = extractor.extract_api_parallel(remaining)
        api_hits = sum(1 for t in api_results if t)
        if api_hits:
            print(f"  [API] Pre-fetched {api_hits}/{len(remaining)} transcripts directly.")

        for i, (lec, api_transcript) in enumerate(zip(remaining, api_results), done + 1):
            print(f"  [{i}/{total}] {lec['title'][:55]}...", end=" ", flush=True)

            if api_transcript:
                transcript = api_transcript.strip()
                strategy = "API"
                # Cache the API result
                extractor._cache.set(lec, transcript)
            else:
                result = extractor.extract(lec)
                transcript = result["transcript"]
                strategy = result["strategy"]

            word_count = len(transcript.split())
            print(f"({word_count} words) [{strategy}]")

            enriched.append({
                "week":       lec["week"],
                "title":      lec["title"],
                "url":        lec["url"],
                "transcript": transcript,
            })

            with open(checkpoint_path, "w", encoding="utf-8") as f:
                json.dump({
                    "course_slug": course_slug,
                    "course_title": course_title,
                    "scraped_at": datetime.now().isoformat(),
                    "lectures": enriched,
                }, f, indent=2, ensure_ascii=False)

            time.sleep(0.1)

        extractor.print_summary()

    finally:
        driver.quit()

    # Build PDF
    build_pdf(course_title, enriched, OUTPUT_PDF)

    print("\n[Success] Done!")
    print(f"    Output file: {os.path.abspath(OUTPUT_PDF)}")


if __name__ == "__main__":
    main()