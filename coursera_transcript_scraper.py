"""
Coursera Transcript Scraper & PDF Generator
============================================
Requirements:
    pip install selenium webdriver-manager reportlab

Browser requirement:
    Google Chrome must be installed on your system.

Usage:
    python coursera_transcript_scraper.py

The script will:
    1. Ask for your Coursera email, password, and course URL
    2. Log in automatically (browser opens visibly so you can handle 2FA if needed)
    3. Scrape all module transcripts in the background
    4. Save everything to a nicely formatted PDF
"""

import os
import sys
import time
import re
import json
import getpass
from datetime import datetime

# ==============================================================================
# Selenium Imports
# ==============================================================================
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager

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


# ==============================================================================
# Configuration
# Edit these defaults or leave blank to be prompted
# ==============================================================================

DEFAULT_EMAIL    = ""          # e.g. "you@gmail.com"
DEFAULT_PASSWORD = ""          # leave blank to be prompted securely
DEFAULT_COURSE_URL = ""        # e.g. "https://www.coursera.org/learn/machine-learning"
OUTPUT_PDF = "coursera_transcripts.pdf"
CHECKPOINT_JSON = "coursera_transcripts_checkpoint.json"

# Set to True to run Chrome in the background (headless).
# NOTE: Coursera's login page sometimes blocks headless browsers.
# Keep False on first run; switch to True once login works.
HEADLESS = False

# Seconds to wait for page elements before timing out
WAIT_TIMEOUT = 20


# ==============================================================================
# Browser Setup
# ==============================================================================

def build_driver(headless: bool = False) -> webdriver.Chrome:
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--window-size=1280,900")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    service = Service(ChromeDriverManager().install())
    driver  = webdriver.Chrome(service=service, options=options)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"}
    )
    return driver


# ==============================================================================
# Login
# ==============================================================================

def login(driver: webdriver.Chrome, email: str, password: str) -> bool:
    print("\n[1/4] Logging in to Coursera...")
    driver.get("https://www.coursera.org/")
    time.sleep(2)

    # Click 'Log In'
    try:
        login_btn = WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//a[contains(@href,'/login') or contains(text(),'Log In')]")
            )
        )
        login_btn.click()
    except TimeoutException:
        driver.get("https://www.coursera.org/login")

    time.sleep(2)

    # Fill email
    try:
        email_field = WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.ID, "email"))
        )
        email_field.clear()
        email_field.send_keys(email)
    except TimeoutException:
        print("  [Error] Could not find email field. Please log in manually in the browser.")
        input("  Press ENTER after you have logged in...")
        return True

    # Fill password
    pwd_field = driver.find_element(By.ID, "password")
    pwd_field.clear()
    pwd_field.send_keys(password)

    # Submit
    driver.find_element(By.XPATH, "//button[@type='submit']").click()
    time.sleep(4)

    # Handle 2FA / CAPTCHA - wait for user if needed
    if "login" in driver.current_url.lower() or "challenge" in driver.current_url.lower():
        print("  [Warning] 2FA or CAPTCHA detected.")
        print("     Complete it in the browser window, then come back here.")
        input("  Press ENTER once you are logged in...")

    print("  [Success] Logged in.")
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
                seen_urls.add(href)
                lectures.append({
                    "week": "Module",
                    "title": a.text.strip() or "Lecture",
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
                    time.sleep(0.2)
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(0.5)
                except Exception:
                    pass
        except Exception:
            pass


# ==============================================================================
# Transcript Extraction
# ==============================================================================

def extract_transcript(driver: webdriver.Chrome, lecture: dict) -> str:
    """Navigate to a lecture page and extract its transcript text."""
    driver.get(lecture["url"])
    time.sleep(3)

    # 1) Look for transcript tab / panel
    transcript_xpaths = [
        "//button[contains(translate(text(),'TRANSCRIPT','transcript'),'transcript')]",
        "//div[contains(@class,'Transcript')]",
        "//*[@data-testid='transcript-panel']",
        "//button[contains(@aria-controls,'transcript')]",
    ]

    # Try clicking a transcript tab if visible
    for xpath in transcript_xpaths[:2]:
        try:
            btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, xpath))
            )
            driver.execute_script("arguments[0].click();", btn)
            time.sleep(1.5)
            break
        except Exception:
            pass

    # 2) Extract text from transcript container
    container_xpaths = [
        "//div[contains(@class,'rc-TranscriptItem') or contains(@class,'transcript')]",
        "//*[@data-testid='transcript']",
        "//div[@role='region' and contains(@aria-label,'transcript')]",
        "//div[contains(@class,'phrases')]",
    ]

    text_parts = []
    for xpath in container_xpaths:
        try:
            elements = driver.find_elements(By.XPATH, xpath)
            if elements:
                text_parts = [el.text.strip() for el in elements if el.text.strip()]
                if text_parts:
                    break
        except Exception:
            pass

    # 3) Fallback: download the .txt transcript file if button exists
    if not text_parts:
        try:
            dl_btn = driver.find_element(
                By.XPATH,
                "//a[contains(@href,'.txt') and contains(translate(@href,'TRANSCRIPT','transcript'),'transcript')]"
            )
            import urllib.request, urllib.error
            txt_url = dl_btn.get_attribute("href")
            cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
            req = urllib.request.Request(txt_url)
            for name, value in cookies.items():
                req.add_header("Cookie", f"{name}={value}")
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                # Strip SRT timestamps if present
                raw = re.sub(r"\d+\n\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}\n", "", raw)
                text_parts = [raw.strip()]
        except Exception:
            pass

    # 4) Absolute fallback: grab all paragraph text on the page
    if not text_parts:
        try:
            paras = driver.find_elements(By.TAG_NAME, "p")
            text_parts = [p.text.strip() for p in paras if len(p.text.strip()) > 40]
        except Exception:
            pass

    transcript = "\n".join(text_parts).strip()
    return transcript if transcript else "(No transcript found for this lecture.)"


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

def main():
    print("=" * 60)
    print("  Coursera Transcript Scraper & PDF Generator")
    print("=" * 60)

    # Gather credentials
    email = DEFAULT_EMAIL or input("\nCoursera email: ").strip()
    password = DEFAULT_PASSWORD or getpass.getpass("Coursera password (hidden): ")
    course_url = DEFAULT_COURSE_URL or input(
        "Course URL (e.g. https://www.coursera.org/learn/machine-learning): "
    ).strip()

    if not re.search(r"coursera\.org/learn/", course_url):
        print("[Warning] That doesn't look like a Coursera course URL. Continuing anyway...")

    course_slug  = get_course_slug(course_url)
    course_title = course_slug.replace("-", " ").title()

    # Launch browser
    print(f"\n  Headless mode: {'ON (background)' if HEADLESS else 'OFF (visible)'}")
    print("  Tip: Set HEADLESS = True at the top of this file to run in background.")
    driver = build_driver(headless=HEADLESS)

    try:
        # Login
        login(driver, email, password)

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
                # Only resume if same course
                if checkpoint_data.get("course_slug") == course_slug:
                    enriched = checkpoint_data.get("lectures", [])
                    scraped_urls = {item.get("url", "") for item in enriched}
                    print(f"\n  [Info] Resuming from checkpoint - {len(enriched)} lecture(s) already scraped.")
            except Exception:
                pass

        # Scrape transcripts
        remaining = [lec for lec in lectures if lec["url"] not in scraped_urls]
        total = len(lectures)
        done = total - len(remaining)
        print(f"\n[3/4] Scraping transcripts for {len(remaining)} remaining lecture(s) (of {total} total)...")

        for i, lec in enumerate(remaining, done + 1):
            print(f"  [{i}/{total}] {lec['title'][:60]}...", end=" ", flush=True)
            transcript = extract_transcript(driver, lec)
            word_count = len(transcript.split())
            print(f"({word_count} words)")
            enriched.append({
                "week":       lec["week"],
                "title":      lec["title"],
                "url":        lec["url"],
                "transcript": transcript,
            })

            # Save checkpoint after each lecture
            with open(checkpoint_path, "w", encoding="utf-8") as f:
                json.dump({
                    "course_slug": course_slug,
                    "course_title": course_title,
                    "scraped_at": datetime.now().isoformat(),
                    "lectures": enriched,
                }, f, indent=2, ensure_ascii=False)

            time.sleep(1)  # polite delay

    finally:
        driver.quit()

    # Build PDF
    build_pdf(course_title, enriched, OUTPUT_PDF)

    print("\n[Success] Done!")
    print(f"    Output file: {os.path.abspath(OUTPUT_PDF)}")
    print(
        "\n[Tip] To run in the background next time:\n"
        "    Set  HEADLESS = True  at the top of the script,\n"
        "      then run: nohup python coursera_transcript_scraper.py &"
    )


if __name__ == "__main__":
    main()