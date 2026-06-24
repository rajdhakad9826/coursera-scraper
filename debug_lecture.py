#!/usr/bin/env python3
"""
Coursera transcript source investigator.
Runs 5 diagnostic phases and saves all findings to debug/.

Usage:
    python debug_lecture.py <lecture_url>
    python debug_lecture.py <lecture_url> --browser firefox
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException

DEBUG = Path("debug")

_TRANSCRIPT_KW = frozenset(
    "transcript caption subtitle cue vtt lecture video asset subtitleAsset graphql".split()
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _save(name: str, content: str) -> None:
    path = DEBUG / name
    path.write_text(content, encoding="utf-8")
    print(f"    saved → debug/{name}")


def _http_from_driver(driver) -> requests.Session:
    s = requests.Session()
    for c in driver.get_cookies():
        s.cookies.set(c["name"], c["value"])
    try:
        s.headers["User-Agent"] = driver.execute_script("return navigator.userAgent") or ""
    except Exception:
        pass
    s.headers["Referer"] = "https://www.coursera.org/"
    return s


def _network_snapshot(driver) -> list[dict]:
    return driver.execute_script(
        "return window.performance.getEntriesByType('resource').map(e => ({name: e.name, type: e.initiatorType, ms: Math.round(e.duration)}))"
    ) or []


def _kw_hit(text: str) -> list[str]:
    low = text.lower()
    return [k for k in _TRANSCRIPT_KW if k in low]


# ---------------------------------------------------------------------------
# Phase 1 — <track> inspection + VTT download
# ---------------------------------------------------------------------------

def phase1_tracks(driver, http: requests.Session) -> bool:
    """Returns True if transcript found in VTT."""
    print("\n── Phase 1: <track> element inspection ──")
    tracks = driver.execute_script("""
        return [...document.querySelectorAll('track')].map(t => ({
            kind:    t.kind,
            src:     t.src || t.getAttribute('src') || '',
            srclang: t.srclang || '',
            label:   t.label  || ''
        }));
    """)

    if not tracks:
        print("  No <track> elements found.")
        return False

    print(f"  Found {len(tracks)} track(s):")
    for t in tracks:
        print(f"    kind={t['kind']!r:12} srclang={t['srclang']!r:6} label={t['label']!r}")
        print(f"    src: {t['src'][:100]}")

    _save("tracks.json", json.dumps(tracks, indent=2))

    # Download first caption/subtitle track
    ordered = (
        [t for t in tracks if t["kind"] == "captions"  and t.get("srclang", "").startswith("en")] or
        [t for t in tracks if t["kind"] == "captions"] or
        [t for t in tracks if t["kind"] == "subtitles"] or
        tracks
    )

    for track in ordered:
        src = track.get("src", "").strip()
        if not src:
            continue
        if not src.startswith("http"):
            src = ("https://www.coursera.org" + src) if src.startswith("/") else src

        print(f"\n  Downloading VTT: {src[:100]}")
        try:
            resp = http.get(src, timeout=10)
            print(f"  HTTP {resp.status_code}  ({len(resp.content)} bytes)")
            if resp.status_code == 200 and resp.text.strip():
                _save("subtitles.vtt", resp.text)
                lines = [l for l in resp.text.splitlines()
                         if l.strip() and "WEBVTT" not in l and "-->" not in l and not l.strip().isdigit()]
                words = sum(len(l.split()) for l in lines)
                print(f"  Word count in VTT: ~{words}")
                print(f"  First 400 chars:\n{'─'*40}")
                print(resp.text[:400])
                print("─" * 40)
                if words > 20:
                    print("  ✓ VTT contains transcript — this is the source!")
                    return True
                else:
                    print("  ✗ VTT too short — probably metadata only")
            else:
                print(f"  ✗ Empty or error response")
        except Exception as exc:
            print(f"  Error: {exc}")

    return False


# ---------------------------------------------------------------------------
# Phase 2 — Network request comparison before/after click
# ---------------------------------------------------------------------------

def phase2_network(driver) -> tuple[list, list]:
    """Returns (before, after) network snapshots. Call this twice around click."""
    return _network_snapshot(driver), []  # before; after filled by phase2_after


def phase2_after(driver, before: list) -> None:
    after = _network_snapshot(driver)
    new = [r for r in after if r not in before]

    _save("network_before.json", json.dumps(before, indent=2))
    _save("network_after.json",  json.dumps(after,  indent=2))
    _save("network_new.json",    json.dumps(new,    indent=2))

    print(f"\n── Phase 2: Network comparison ──")
    print(f"  Before: {len(before)} requests  After: {len(after)}  New: {len(new)}")

    relevant = [r for r in new if _kw_hit(r.get("name", ""))]
    if relevant:
        print(f"  Transcript-related new requests ({len(relevant)}):")
        for r in relevant:
            print(f"    [{r.get('ms', '?')}ms] {r['name'][:120]}")
    else:
        print("  No transcript-related new requests detected.")


# ---------------------------------------------------------------------------
# Phase 3 — MutationObserver before/after transcript button click
# ---------------------------------------------------------------------------

def phase3_mutation(driver) -> None:
    print("\n── Phase 3: MutationObserver + transcript button click ──")

    driver.execute_script("""
        window._mutations = [];
        new MutationObserver(records => {
            records.forEach(r => {
                window._mutations.push({
                    type:    r.type,
                    target:  r.target.tagName +
                             (r.target.id ? '#'+r.target.id : '') +
                             (r.target.className ? '.'+String(r.target.className).trim().split(/\\s+/)[0] : ''),
                    added:   r.addedNodes.length,
                    removed: r.removedNodes.length,
                    attr:    r.attributeName
                });
            });
        }).observe(document.body, {childList:true, subtree:true, attributes:true});
    """)

    btn = None
    for by, sel in [
        (By.CSS_SELECTOR, "button[data-testid='item-tool-panel-button-transcript']"),
        (By.CSS_SELECTOR, "button[data-testid*='transcript']"),
        (By.CSS_SELECTOR, ".css-ie967v"),
        (By.XPATH, "//button[contains(translate(normalize-space(.),'TRANSCRIPT','transcript'),'transcript')]"),
    ]:
        try:
            btn = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((by, sel)))
            print(f"  Transcript button found via: {sel}")
            break
        except TimeoutException:
            pass

    if not btn:
        print("  Transcript button not found — skipping click phases")
        return

    btn.click()
    time.sleep(2)

    mutations = driver.execute_script("return window._mutations || []")
    _save("mutations.json", json.dumps(mutations, indent=2))

    added   = sum(m.get("added",   0) for m in mutations)
    removed = sum(m.get("removed", 0) for m in mutations)
    unique  = sorted({m.get("target", "") for m in mutations})
    print(f"  DOM mutations: {len(mutations)}  (nodes added: {added}, removed: {removed})")
    print(f"  Affected elements: {unique[:15]}")

    # Check if transcript text appeared in DOM after click
    try:
        page = driver.page_source
        kw_hits = _kw_hit(page)
        print(f"  Keywords in page source after click: {kw_hits}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Phase 4 — React / Apollo / Next.js embedded state
# ---------------------------------------------------------------------------

def phase4_react_state(driver) -> None:
    print("\n── Phase 4: Embedded app state inspection ──")

    for var in ("window.__APOLLO_STATE__", "window.__INITIAL_STATE__", "window.__NEXT_DATA__", "window.__NUXT__"):
        try:
            raw = driver.execute_script(
                f"return typeof {var}!=='undefined' ? JSON.stringify({var}).substring(0,8000) : null"
            )
            if raw:
                hits = _kw_hit(raw)
                fname = var.replace("window.", "").replace("__", "").lower() + ".json"
                _save(fname, raw)
                print(f"  {var}: {len(raw)} chars  keywords: {hits or 'none'}")
            else:
                print(f"  {var}: not present")
        except Exception as exc:
            print(f"  {var}: error — {exc}")

    # Also check window._cr (captured XHR/fetch from interceptor if set up)
    try:
        captured = driver.execute_script("return window._cr || []")
        if captured:
            _save("captured_xhr.json", json.dumps(captured, indent=2))
            print(f"  window._cr (XHR interceptor): {len(captured)} captured responses")
            for r in captured:
                hits = _kw_hit(r.get("url", ""))
                if hits:
                    print(f"    {r['url'][:100]} — keywords: {hits}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Phase 6 — Playback-triggered track discovery
# ---------------------------------------------------------------------------

_VIDEO_EVENT_SCRIPT = """
window._videoEvents = [];
const v = document.querySelector('video');
if (v) {
    ['play','playing','loadedmetadata','loadeddata','canplay','canplaythrough','timeupdate'].forEach(e => {
        v.addEventListener(e, () => window._videoEvents.push({event: e, ts: Date.now()}), {once: true});
    });
}
return v !== null;
"""

_PLAY_SELECTORS = [
    (By.CSS_SELECTOR, "button[aria-label='Play']"),
    (By.CSS_SELECTOR, "button[aria-label='play']"),
    (By.XPATH,        "//button[contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'play')]"),
    (By.CSS_SELECTOR, ".vjs-play-control"),
    (By.CSS_SELECTOR, "[data-testid='play-button']"),
    (By.CSS_SELECTOR, "[data-testid*='play']"),
]


def phase6_playback_tracks(driver, http: requests.Session) -> bool:
    """
    Click transcript button, trigger playback, watch for lazy-loaded tracks.
    Returns True if VTT found after playback.
    """
    print("\n── Phase 6: Playback-triggered track discovery ──")

    # Inject video event listeners
    has_video = driver.execute_script(_VIDEO_EVENT_SCRIPT)
    if not has_video:
        print("  No <video> element found.")
        return False
    print("  Video element found. Event listeners attached.")

    # Open transcript panel
    for by, sel in [
        (By.CSS_SELECTOR, "button[data-testid='item-tool-panel-button-transcript']"),
        (By.CSS_SELECTOR, "button[data-testid*='transcript']"),
    ]:
        try:
            btn = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((by, sel)))
            btn.click()
            print(f"  Opened transcript panel via: {sel}")
            time.sleep(0.5)
            break
        except TimeoutException:
            pass

    # Snapshot network before play
    net_before_play = _network_snapshot(driver)

    # Click play
    play_triggered = False
    for by, sel in _PLAY_SELECTORS:
        try:
            btn = WebDriverWait(driver, 4).until(EC.element_to_be_clickable((by, sel)))
            btn.click()
            print(f"  Play triggered via: {sel}")
            play_triggered = True
            break
        except TimeoutException:
            pass

    if not play_triggered:
        result = driver.execute_script(
            "const v=document.querySelector('video'); if(v){v.play();return true;} return false;"
        )
        if result:
            print("  Play triggered via JS video.play()")
            play_triggered = True
        else:
            print("  Could not trigger playback.")

    if not play_triggered:
        return False

    # Poll for tracks up to 5s, logging any new appearances
    print("  Waiting up to 5s for <track> elements...")
    found_tracks = []
    deadline = time.time() + 5.0
    while time.time() < deadline:
        tracks = driver.execute_script("""
            return [...document.querySelectorAll('track')].map(t => ({
                kind: t.kind, src: t.src||t.getAttribute('src')||'',
                srclang: t.srclang||'', label: t.label||''
            }));
        """) or []
        caption_tracks = [t for t in tracks if t["kind"] in ("captions", "subtitles") and t.get("src")]
        if caption_tracks and caption_tracks != found_tracks:
            found_tracks = caption_tracks
            elapsed = 5.0 - (deadline - time.time())
            print(f"  {len(found_tracks)} caption track(s) appeared at +{elapsed:.1f}s")
            for t in found_tracks:
                print(f"    [{t['kind']}] {t['srclang']} — {t['src'][:80]}")
        if found_tracks:
            break
        time.sleep(0.3)

    # Video events
    events = driver.execute_script("return window._videoEvents || []")
    if events:
        print(f"  Video events fired ({len(events)}): {[e['event'] for e in events]}")
    else:
        print("  No video events captured.")

    # Network after play
    net_after_play = _network_snapshot(driver)
    new_play_reqs = [r for r in net_after_play if r not in net_before_play]
    relevant_play = [r for r in new_play_reqs if _kw_hit(r.get("name", ""))]
    _save("network_play_before.json", json.dumps(net_before_play, indent=2))
    _save("network_play_after.json",  json.dumps(net_after_play,  indent=2))
    if relevant_play:
        print(f"  Transcript-related requests after play ({len(relevant_play)}):")
        for r in relevant_play:
            print(f"    [{r.get('ms', '?')}ms] {r['name'][:120]}")
    else:
        print("  No transcript-related new requests after play.")

    if not found_tracks:
        print("  No tracks appeared after playback.")
        return False

    # Download first caption track
    _save("tracks_after_play.json", json.dumps(found_tracks, indent=2))
    track = found_tracks[0]
    src = track["src"].strip()
    if not src.startswith("http"):
        src = ("https://www.coursera.org" + src) if src.startswith("/") else src

    print(f"\n  Downloading VTT after playback: {src[:100]}")
    try:
        resp = http.get(src, timeout=10)
        print(f"  HTTP {resp.status_code}  ({len(resp.content)} bytes)")
        if resp.status_code == 200 and resp.text.strip():
            _save("subtitles_after_play.vtt", resp.text)
            lines = [l for l in resp.text.splitlines()
                     if l.strip() and "WEBVTT" not in l and "-->" not in l and not l.strip().isdigit()]
            words = sum(len(l.split()) for l in lines)
            print(f"  Word count: ~{words}")
            if words > 20:
                print("  ✓ VTT transcript confirmed after playback trigger!")
                print("  ✓ PlaybackVTTExtractor (Level 2) will handle this.")
                return True
    except Exception as exc:
        print(f"  Error downloading VTT: {exc}")

    return False


# ---------------------------------------------------------------------------
# Phase 5 — DOM snapshot: extract transcript text if visible
# ---------------------------------------------------------------------------

def phase5_dom_snapshot(driver) -> None:
    print("\n── Phase 5: Post-click DOM snapshot ──")
    try:
        page = driver.page_source
        _save("page_after_click.html", page)
        print(f"  Page source: {len(page)} chars → debug/page_after_click.html")
    except Exception as exc:
        print(f"  Could not save page source: {exc}")

    # Look for transcript-related elements in DOM
    for by, sel, desc in [
        (By.XPATH, "//*[@data-testid='transcript-panel']",                           "data-testid=transcript-panel"),
        (By.CSS_SELECTOR, "[class*='TranscriptPanel']",                              "class~TranscriptPanel"),
        (By.XPATH, "//*[@role='region' and contains(translate(@aria-label,'TRANSCRIPT','transcript'),'transcript')]", "role=region"),
        (By.CSS_SELECTOR, "[class*='phrases']",                                      "class~phrases"),
        (By.XPATH, "//*[contains(@class,'TranscriptItem')]",                         "class~TranscriptItem"),
        (By.XPATH, "//span[@data-start]",                                            "span[data-start]"),
    ]:
        try:
            els = driver.find_elements(by, sel)
            if els:
                sample = els[0].text[:100].replace("\n", " ")
                print(f"  {desc}: {len(els)} element(s) — sample: {sample!r}")
        except Exception:
            pass

    # Screenshot
    try:
        driver.save_screenshot(str(DEBUG / "screenshot.png"))
        print("  Screenshot saved → debug/screenshot.png")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Investigate Coursera transcript source")
    parser.add_argument("url", help="Lecture URL (https://www.coursera.org/learn/...)")
    parser.add_argument("--browser", default=None, help="Browser: chrome, firefox, edge, chromium")
    args = parser.parse_args()

    DEBUG.mkdir(exist_ok=True)
    print(f"\nDebug output directory: {DEBUG.absolute()}")

    from browser_manager import BrowserManager, Browser
    from session_manager import SessionManager

    mgr = BrowserManager()
    if args.browser:
        try:
            browser = Browser(args.browser.lower())
        except ValueError:
            print(f"Unknown browser: {args.browser}"); sys.exit(1)
    else:
        browser = mgr.get_default_browser()
        if not browser:
            print("No supported browser found."); sys.exit(1)

    print(f"Browser: {browser.value}")

    session = SessionManager(browser.value)
    driver = mgr.create_driver(browser, headless=False, profile_dir=session.profile_dir)

    try:
        # Auth
        print("\nChecking authentication...")
        if session.is_logged_in(driver):
            print("  Logged in via saved session.")
        else:
            driver.get("https://www.coursera.org/login")
            print("  Log in to Coursera, then press ENTER.")
            input("  > ")

        # Navigate to lecture
        print(f"\nNavigating: {args.url}")
        driver.get(args.url)
        time.sleep(3)  # let video player initialise

        http = _http_from_driver(driver)

        # Snapshot network before any interaction
        net_before = _network_snapshot(driver)

        # Phase 1 — VTT tracks (passive, before any interaction)
        vtt_found = phase1_tracks(driver, http)
        if vtt_found:
            print("\n✓ CONCLUSION: VTT track present before playback.")
            print("  VTTExtractor (Level 1) handles this. No interaction needed.")
        else:
            print("\n  Phase 1: no tracks yet — checking if playback triggers them.")

            # Phase 6 — Playback-triggered track discovery
            playback_vtt_found = phase6_playback_tracks(driver, http)
            if playback_vtt_found:
                print("\n✓ CONCLUSION: Transcript lazy-loads when playback begins.")
                print("  PlaybackVTTExtractor (Level 2) will handle this.")
            else:
                # Phase 4 — React state (before click)
                phase4_react_state(driver)

                # Phase 3 — MutationObserver + transcript click
                phase3_mutation(driver)
                time.sleep(1)

                # Phase 2 — Network after click
                phase2_after(driver, net_before)

                # Phase 5 — DOM snapshot after click
                phase5_dom_snapshot(driver)

                print("\n? CONCLUSION: VTT not found, even after playback.")
                print("  Check network_new.json / network_play_after.json for transcript source.")

        print(f"\nAll debug files in: {DEBUG.absolute()}")
        input("\nPress ENTER to close browser...")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
