"""
Transcript extraction — 7-level cascade, fastest first.

Level 1  VTTExtractor           <track> element → subtitleAssetProxy VTT download (passive)
Level 2  PlaybackVTTExtractor   Trigger playback, wait for lazy-loaded <track> elements, retry VTT
Level 3  NetworkExtractor       XHR/fetch responses captured by CDP injection
Level 4  ApiExtractor           Coursera REST API via requests + cookies (parallelisable)
Level 5  EmbeddedJsonExtractor  window.__APOLLO_STATE__ / <script type=application/json>
Level 6  DomExtractor           Transcript already in DOM, no interaction
Level 7  SeleniumFallback       Click button, wait, scrape panel

Discovery: Coursera lazy-loads <track kind="captions"> elements when video playback begins.
Level 1 succeeds if tracks are pre-loaded; Level 2 triggers playback to force their appearance.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests as _requests
from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TRANSCRIPT_KEYS = frozenset({
    "transcript", "transcriptText", "subtitlesTxt", "transcriptV2",
    "caption", "captions", "subtitle", "subtitles", "vttText",
})
_PHRASE_KEYS = frozenset({"phrase", "phrases", "cue", "cues"})

COURSERA_TRANSCRIPT_API = (
    "https://www.coursera.org/api/onDemandLectureTranscripts.v1/{lecture_id}?q=get"
)

# JS injected before every page load — captures matching XHR/fetch response bodies
_NETWORK_INTERCEPT_SCRIPT = r"""
window._cr = [];
const _k = /transcript|caption|subtitle|onDemandLecture/i;
const _xo = XMLHttpRequest.prototype.open;
XMLHttpRequest.prototype.open = function(m, u) {
  const _u = String(u);
  this.addEventListener('load', function() {
    if (_k.test(_u)) { try { window._cr.push({url: _u, body: this.responseText}); } catch(e){} }
  });
  _xo.apply(this, arguments);
};
const _f = window.fetch;
window.fetch = async function(...a) {
  const r = await _f(...a);
  const u = String(typeof a[0]==='string' ? a[0] : (a[0]?.url||''));
  if (_k.test(u)) { r.clone().text().then(b=>{ try{window._cr.push({url:u,body:b});}catch(e){} }); }
  return r;
};
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_lecture_id(url: str) -> Optional[str]:
    m = re.search(r"/lecture/([^/?#]+)", url)
    return m.group(1) if m else None


def _parse_vtt(text: str) -> str:
    """Parse VTT/SRT to plain text. Removes timestamps, deduplicates overlapping cues."""
    lines: list[str] = []
    buf: list[str] = []
    prev = ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("WEBVTT") or "-->" in line or line.isdigit() or line.startswith("NOTE"):
            if buf:
                phrase = " ".join(buf)
                if phrase != prev:  # VTT files often repeat phrases in overlapping cues
                    lines.append(phrase)
                    prev = phrase
                buf = []
        else:
            clean = re.sub(r"<[^>]+>", "", line)   # remove inline VTT tags e.g. <c.colorE5E5E5>
            clean = re.sub(r"\{[^}]*\}", "", clean) # remove curly-brace annotations
            if clean.strip():
                buf.append(clean.strip())
    if buf:
        phrase = " ".join(buf)
        if phrase != prev:
            lines.append(phrase)
    return "\n".join(lines)


def _safe_filename(text: str) -> str:
    return re.sub(r"[^\w\-]", "_", text)[:50]


def _make_requests_session(driver: webdriver.Remote) -> _requests.Session:
    s = _requests.Session()
    for c in driver.get_cookies():
        s.cookies.set(c["name"], c["value"])
    try:
        ua = driver.execute_script("return navigator.userAgent") or ""
    except Exception:
        ua = ""
    s.headers.update({
        "User-Agent": ua or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.coursera.org/",
    })
    return s


def _extract_phrases(obj) -> Optional[str]:
    if not isinstance(obj, list):
        return None
    texts = []
    for item in obj:
        if isinstance(item, str) and len(item.strip()) > 2:
            texts.append(item.strip())
        elif isinstance(item, dict):
            for key in ("text", "content", "value"):
                v = item.get(key, "")
                if isinstance(v, str) and len(v.strip()) > 2:
                    texts.append(v.strip())
                    break
    return "\n".join(texts) if len(texts) >= 3 else None


def _search_transcript_in_json(obj, _depth: int = 0) -> Optional[str]:
    """Recursively search JSON for transcript text."""
    if _depth > 8 or obj is None:
        return None
    if isinstance(obj, str):
        # Multi-line string with substantial content = likely transcript
        if len(obj) > 200 and "\n" in obj[:500]:
            return obj
        return None
    if isinstance(obj, dict):
        for key in _TRANSCRIPT_KEYS:
            val = obj.get(key)
            if isinstance(val, str) and len(val) > 50:
                return val
            if isinstance(val, list):
                r = _extract_phrases(val)
                if r:
                    return r
        for key in _PHRASE_KEYS:
            val = obj.get(key)
            if val:
                r = _extract_phrases(val)
                if r:
                    return r
        for val in obj.values():
            r = _search_transcript_in_json(val, _depth + 1)
            if r:
                return r
    if isinstance(obj, list):
        for item in obj:
            r = _search_transcript_in_json(item, _depth + 1)
            if r:
                return r
    return None


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

class TranscriptCache:
    def __init__(self, cache_dir: str = ".cache") -> None:
        self._dir = Path(cache_dir)
        self._dir.mkdir(exist_ok=True)

    def _path(self, lecture: dict) -> Path:
        h = hashlib.md5(lecture["url"].encode()).hexdigest()
        return self._dir / f"{h}.txt"

    def get(self, lecture: dict) -> Optional[str]:
        p = self._path(lecture)
        return p.read_text(encoding="utf-8") if p.exists() else None

    def set(self, lecture: dict, transcript: str) -> None:
        self._path(lecture).write_text(transcript, encoding="utf-8")


# ---------------------------------------------------------------------------
# Base extractor
# ---------------------------------------------------------------------------

class BaseExtractor(ABC):
    name: str = "BASE"

    @abstractmethod
    def extract(
        self,
        driver: Optional[webdriver.Remote],
        lecture: dict,
        http_session: Optional[_requests.Session] = None,
    ) -> Optional[str]: ...


# ---------------------------------------------------------------------------
# Level 1 — VTT track extraction (fastest: reads <track> elements, HTTP fetch)
# ---------------------------------------------------------------------------

class VTTExtractor(BaseExtractor):
    """
    Reads <track kind="captions|subtitles"> elements already in the video player DOM,
    downloads the VTT file via requests (no Selenium interaction), parses to text.

    Coursera serves captions via /api/subtitleAssetProxy.v1/... — a direct HTTP fetch
    with session cookies is enough. No button clicks, no panel rendering.
    """
    name = "VTT"

    def extract(
        self,
        driver,
        lecture: dict,
        http_session: Optional[_requests.Session] = None,
    ) -> Optional[str]:
        if driver is None or not http_session:
            return None
        try:
            tracks = driver.execute_script("""
                return [...document.querySelectorAll('track')].map(t => ({
                    kind:    t.kind,
                    src:     t.src || t.getAttribute('src') || '',
                    srclang: t.srclang || '',
                    label:   t.label  || ''
                }));
            """)
        except Exception as exc:
            log.debug("VTT: track query failed: %s", exc)
            return None

        if not tracks:
            return None

        log.debug("VTT: %d track(s) found", len(tracks))

        # Priority: English captions → any captions → subtitles → anything
        ordered = (
            [t for t in tracks if t.get("kind") == "captions"  and t.get("srclang", "").startswith("en")] or
            [t for t in tracks if t.get("kind") == "captions"] or
            [t for t in tracks if t.get("kind") == "subtitles"] or
            tracks
        )

        for track in ordered:
            src = track.get("src", "").strip()
            if not src:
                continue
            if not src.startswith("http"):
                src = ("https://www.coursera.org" + src) if src.startswith("/") else src
            transcript = self._fetch(src, http_session)
            if transcript:
                return transcript

        return None

    def _fetch(self, url: str, session: _requests.Session) -> Optional[str]:
        try:
            resp = session.get(url, timeout=10)
            if resp.status_code == 200 and resp.text.strip():
                return _parse_vtt(resp.text) or None
            log.debug("VTT: HTTP %d for %s", resp.status_code, url[:80])
        except Exception as exc:
            log.debug("VTT fetch failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Level 2 — Playback-triggered VTT (lazy-loaded captions)
# ---------------------------------------------------------------------------

_PLAY_SELECTORS = [
    (By.CSS_SELECTOR, "button[aria-label='Play']"),
    (By.CSS_SELECTOR, "button[aria-label='play']"),
    (By.XPATH,        "//button[contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'play')]"),
    (By.CSS_SELECTOR, ".vjs-play-control"),
    (By.CSS_SELECTOR, "[data-testid='play-button']"),
    (By.CSS_SELECTOR, "[data-testid*='play']"),
]


class PlaybackVTTExtractor(BaseExtractor):
    """
    Coursera lazy-loads <track kind="captions"> when video playback begins.
    Opens transcript panel, triggers playback, polls for <track> elements
    (up to 5s), then delegates to VTTExtractor.
    """
    name = "PLAYBACK_VTT"
    _vtt = VTTExtractor()

    def extract(self, driver, lecture, http_session=None, **kwargs) -> Optional[str]:
        if driver is None or not http_session:
            return None
        self._open_transcript_panel(driver)
        if not self._trigger_playback(driver):
            log.debug("PLAYBACK_VTT: could not trigger playback")
            return None
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if self._tracks_present(driver):
                log.debug("PLAYBACK_VTT: tracks appeared after playback")
                return self._vtt.extract(driver, lecture, http_session=http_session)
            time.sleep(0.5)
        log.debug("PLAYBACK_VTT: no tracks after 5s")
        return None

    def _open_transcript_panel(self, driver) -> None:
        for by, sel, _ in _BUTTON_SELECTORS[:2]:
            try:
                btn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((by, sel)))
                btn.click()
                time.sleep(0.3)
                return
            except (TimeoutException, Exception):
                pass

    def _trigger_playback(self, driver) -> bool:
        for by, sel in _PLAY_SELECTORS:
            try:
                btn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((by, sel)))
                btn.click()
                log.debug("PLAYBACK_VTT: play via %s", sel)
                return True
            except (TimeoutException, Exception):
                pass
        try:
            result = driver.execute_script(
                "const v=document.querySelector('video'); if(v){v.play();return true;} return false;"
            )
            if result:
                log.debug("PLAYBACK_VTT: play via JS video.play()")
                return True
        except Exception:
            pass
        return False

    def _tracks_present(self, driver) -> bool:
        try:
            count = driver.execute_script(
                "return [...document.querySelectorAll('track')]"
                ".filter(t=>(t.kind==='captions'||t.kind==='subtitles')&&(t.src||t.getAttribute('src')))"
                ".length;"
            ) or 0
            return count > 0
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Level 3 — Network response interception (CDP)
# ---------------------------------------------------------------------------

class NetworkExtractor(BaseExtractor):
    """Reads responses captured by the injected XHR/fetch interceptor."""
    name = "NETWORK"

    def extract(self, driver, lecture, **kwargs) -> Optional[str]:
        if driver is None:
            return None
        try:
            responses = driver.execute_script("return window._cr || []")
            for r in responses:
                transcript = self._parse(r.get("url", ""), r.get("body", ""))
                if transcript:
                    log.debug("NETWORK hit from %s", r["url"])
                    return transcript
        except Exception as exc:
            log.debug("NETWORK error: %s", exc)
        return None

    def _parse(self, url: str, body: str) -> Optional[str]:
        if not body:
            return None
        if "WEBVTT" in body[:30] or ("-->" in body and "\n" in body):
            return _parse_vtt(body) or None
        try:
            data = json.loads(body)
            return _search_transcript_in_json(data)
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Level 2 — Direct Coursera REST API (no browser rendering)
# ---------------------------------------------------------------------------

class ApiExtractor(BaseExtractor):
    """
    Calls Coursera's onDemandLectureTranscripts.v1 endpoint directly.
    Thread-safe — use with extract_api_parallel() for concurrent extraction.
    """
    name = "API"

    def extract(
        self,
        driver,
        lecture: dict,
        http_session: Optional[_requests.Session] = None,
    ) -> Optional[str]:
        if not http_session:
            return None
        lecture_id = _parse_lecture_id(lecture["url"])
        if not lecture_id:
            return None
        url = COURSERA_TRANSCRIPT_API.format(lecture_id=lecture_id)
        try:
            resp = http_session.get(url, timeout=10)
            if resp.status_code != 200:
                log.debug("API %d for %s", resp.status_code, lecture_id)
                return None
            data = resp.json()
            transcript = _search_transcript_in_json(data)
            if transcript:
                return transcript
            # Look for VTT/txt URL in response, fetch it
            vtt_url = self._find_url(data)
            if vtt_url:
                return self._fetch_vtt(vtt_url, http_session)
        except Exception as exc:
            log.debug("API error: %s", exc)
        return None

    def _find_url(self, obj, _depth: int = 0) -> Optional[str]:
        if _depth > 6 or not obj:
            return None
        if isinstance(obj, str):
            if obj.startswith("http") and (obj.endswith(".vtt") or "transcript" in obj.lower()):
                return obj
        if isinstance(obj, dict):
            for key in ("transcriptUrl", "subtitlesTxtUrl", "vttUrl", "subtitleUrl", "transcriptV2Url"):
                val = obj.get(key)
                if isinstance(val, str) and val.startswith("http"):
                    return val
            for v in obj.values():
                r = self._find_url(v, _depth + 1)
                if r:
                    return r
        if isinstance(obj, list):
            for item in obj:
                r = self._find_url(item, _depth + 1)
                if r:
                    return r
        return None

    def _fetch_vtt(self, url: str, session: _requests.Session) -> Optional[str]:
        try:
            resp = session.get(url, timeout=10)
            if resp.status_code == 200:
                return _parse_vtt(resp.text) or None
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Level 3 — Embedded JSON (Apollo/Redux state, <script type=application/json>)
# ---------------------------------------------------------------------------

class EmbeddedJsonExtractor(BaseExtractor):
    name = "EMBEDDED_JSON"

    _JS_VARS = (
        "window.__APOLLO_STATE__",
        "window.__INITIAL_STATE__",
        "window.__NUXT__",
        "window.__REDUX_STATE__",
    )

    def extract(self, driver, lecture, **kwargs) -> Optional[str]:
        if driver is None:
            return None
        for var in self._JS_VARS:
            try:
                data = driver.execute_script(f"return {var};")
                if data:
                    r = _search_transcript_in_json(data)
                    if r:
                        log.debug("EMBEDDED_JSON hit from %s", var)
                        return r
            except Exception:
                pass
        try:
            page = driver.page_source
            for m in re.finditer(
                r'<script[^>]+type=["\']application/json["\'][^>]*>(.*?)</script>',
                page, re.DOTALL,
            ):
                try:
                    r = _search_transcript_in_json(json.loads(m.group(1)))
                    if r:
                        return r
                except Exception:
                    pass
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Level 4 — DOM extraction (no button clicks)
# ---------------------------------------------------------------------------

class DomExtractor(BaseExtractor):
    name = "DOM"

    _CONTAINERS = [
        (By.XPATH,
         "//*[@role='region' and contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'transcript')]"),
        (By.CSS_SELECTOR, "[data-testid='transcript-panel']"),
        (By.CSS_SELECTOR, "[class*='TranscriptPanel']"),
        (By.CSS_SELECTOR, "[class*='transcriptPanel']"),
        (By.XPATH, "//*[contains(@class,'TranscriptItem')]"),
    ]
    _FRAGMENTS = [
        (By.XPATH, ".//span[@data-start]"),
        (By.XPATH, ".//*[contains(@class,'phrase')]"),
        (By.XPATH, ".//p"),
    ]

    def extract(self, driver, lecture, **kwargs) -> Optional[str]:
        if driver is None:
            return None
        for by, sel in self._CONTAINERS:
            try:
                container = driver.find_element(by, sel)
                texts = self._fragments(container)
                if texts:
                    return "\n".join(texts)
            except (NoSuchElementException, Exception):
                pass
        return None

    def _fragments(self, container) -> list[str]:
        for by, sel in self._FRAGMENTS:
            try:
                els = container.find_elements(by, sel)
                texts = [e.text.strip() for e in els if e.text.strip()]
                if texts:
                    return texts
            except Exception:
                pass
        text = container.text.strip()
        return [text] if text else []


# ---------------------------------------------------------------------------
# Level 5 — Selenium click-and-scrape fallback
# ---------------------------------------------------------------------------

_BUTTON_SELECTORS: list[tuple[str, str, str]] = [
    (By.CSS_SELECTOR, "button[data-testid='item-tool-panel-button-transcript']", "data-testid=item-tool-panel-button-transcript"),
    (By.CSS_SELECTOR, "button[data-testid*='transcript']",                       "data-testid~transcript"),
    (By.XPATH,
     "//button[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'show transcript')]",
     "text='show transcript'"),
    (By.XPATH,
     "//button[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'transcript')]",
     "text='transcript'"),
    (By.XPATH,
     "//button[contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'transcript')]",
     "aria-label~transcript"),
    (By.CSS_SELECTOR, ".css-ie967v", "class=.css-ie967v"),
    (By.XPATH,
     "/html/body/div[2]/div/div[1]/div/div/div[2]/div[2]/div/div/div/div[2]/div/div/button[1]",
     "absolute-xpath"),
]

_CONTAINER_SELECTORS: list[tuple[str, str, str]] = [
    (By.XPATH,
     "//*[@role='region' and contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'transcript')]",
     "role=region aria-label~transcript"),
    (By.XPATH, "//*[@data-testid='transcript-panel']", "data-testid=transcript-panel"),
    (By.CSS_SELECTOR, "[class*='TranscriptPanel']",    "class~TranscriptPanel"),
    (By.XPATH, "//*[contains(@class,'TranscriptItem')]","class~TranscriptItem"),
    (By.CSS_SELECTOR, "[class*='phrases']",             "class~phrases"),
    (By.XPATH,
     "/html/body/div[2]/div/div[1]/div/div/div[2]/div[2]/div/div/div/div[1]/div/div[3]/div/div/div[2]/div/div/div/div/div[1]/div[1]/div/div[2]",
     "absolute-xpath"),
]

_FRAGMENT_SELECTORS: list[tuple[str, str]] = [
    (By.XPATH, ".//span[@data-start]"),
    (By.XPATH, ".//*[contains(@class,'phrase')]"),
    (By.XPATH, ".//*[contains(@class,'cue')]"),
    (By.XPATH, ".//p"),
    (By.XPATH, ".//span[normalize-space(text())]"),
]


class SeleniumFallbackExtractor(BaseExtractor):
    name = "SELENIUM_FALLBACK"

    def extract(self, driver, lecture, **kwargs) -> Optional[str]:
        if driver is None:
            return None
        btn = self._find_button(driver)
        if btn:
            self._click(driver, btn)
            self._wait_panel(driver)
        container = self._find_container(driver)
        if container:
            texts = self._fragments(container)
            if texts:
                return "\n".join(texts)
        return self._txt_fallback(driver) or self._para_fallback(driver) or None

    def _find_button(self, driver) -> Optional[object]:
        for by, sel, desc in _BUTTON_SELECTORS:
            try:
                btn = WebDriverWait(driver, 4).until(EC.element_to_be_clickable((by, sel)))
                log.debug("SELENIUM button: %s", desc)
                return btn
            except (TimeoutException, NoSuchElementException):
                pass
        return None

    def _click(self, driver, btn) -> None:
        try:
            btn.click()
        except (ElementClickInterceptedException, Exception):
            try:
                driver.execute_script("arguments[0].click();", btn)
            except Exception as exc:
                log.debug("Click failed: %s", exc)

    def _wait_panel(self, driver) -> None:
        for by, sel, _ in _CONTAINER_SELECTORS[:-1]:
            try:
                WebDriverWait(driver, 4).until(EC.visibility_of_element_located((by, sel)))
                return
            except TimeoutException:
                pass

    def _find_container(self, driver) -> Optional[object]:
        for by, sel, desc in _CONTAINER_SELECTORS:
            try:
                el = WebDriverWait(driver, 4).until(EC.presence_of_element_located((by, sel)))
                log.debug("SELENIUM container: %s", desc)
                return el
            except (TimeoutException, NoSuchElementException):
                pass
        return None

    def _fragments(self, container) -> list[str]:
        for by, sel in _FRAGMENT_SELECTORS:
            try:
                els = container.find_elements(by, sel)
                texts = [e.text.strip() for e in els if e.text.strip()]
                if texts:
                    return texts
            except (StaleElementReferenceException, Exception):
                pass
        text = container.text.strip()
        return [text] if text else []

    def _txt_fallback(self, driver) -> Optional[str]:
        import urllib.request as _ur
        try:
            btn = driver.find_element(
                By.XPATH,
                "//a[contains(@href,'.txt') and contains(translate(@href,'TRANSCRIPT','transcript'),'transcript')]",
            )
            txt_url = btn.get_attribute("href")
            cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
            req = _ur.Request(txt_url)
            for n, v in cookies.items():
                req.add_header("Cookie", f"{n}={v}")
            with _ur.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            raw = re.sub(r"\d+\n\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}\n", "", raw)
            return raw.strip() or None
        except Exception:
            return None

    def _para_fallback(self, driver) -> Optional[str]:
        try:
            paras = driver.find_elements(By.TAG_NAME, "p")
            texts = [p.text.strip() for p in paras if len(p.text.strip()) > 40]
            return "\n".join(texts) or None
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class TranscriptExtractor:
    """
    Cascade through 5 extractors (fastest first). Reuse one instance across
    all lectures — call setup() once after driver creation.
    """

    def __init__(self, driver: webdriver.Remote, debug_dir: str = "debug") -> None:
        self.driver = driver
        self.debug_dir = Path(debug_dir)
        self._cache = TranscriptCache()
        self._http: Optional[_requests.Session] = None
        self._strategies: list[BaseExtractor] = [
            VTTExtractor(),
            PlaybackVTTExtractor(),
            NetworkExtractor(),
            ApiExtractor(),
            EmbeddedJsonExtractor(),
            DomExtractor(),
            SeleniumFallbackExtractor(),
        ]
        self._metrics: dict = {
            "CACHE": 0, "VTT": 0, "PLAYBACK_VTT": 0, "NETWORK": 0, "API": 0,
            "EMBEDDED_JSON": 0, "DOM": 0, "SELENIUM_FALLBACK": 0,
            "FAILED": 0, "total_time": 0.0,
        }

    def setup(self) -> None:
        """Inject XHR/fetch interceptors. Call once before first driver.get()."""
        try:
            self.driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": _NETWORK_INTERCEPT_SCRIPT},
            )
            log.debug("Network interceptor injected via CDP")
        except Exception:
            log.debug("CDP not available — network interception skipped (Firefox?)")

    def _http_session(self) -> Optional[_requests.Session]:
        if self._http is None:
            try:
                self._http = _make_requests_session(self.driver)
            except Exception:
                pass
        return self._http

    def extract(self, lecture: dict) -> dict:
        """Returns {"video_title", "transcript", "strategy"}."""
        title = lecture.get("title", "untitled")

        cached = self._cache.get(lecture)
        if cached:
            self._metrics["CACHE"] += 1
            log.info("[%s]  CACHE", title[:50])
            return {"video_title": title, "transcript": cached, "strategy": "CACHE"}

        driver = self.driver
        # Reset captured network responses before navigating
        try:
            driver.execute_script("if(window._cr) window._cr = [];")
        except Exception:
            pass

        driver.get(lecture["url"])
        self._wait_ready(driver)

        http = self._http_session()
        t0 = time.perf_counter()

        for strategy in self._strategies:
            transcript = strategy.extract(driver, lecture, http_session=http)
            if transcript and transcript.strip():
                elapsed = time.perf_counter() - t0
                name = strategy.name
                self._metrics[name] = self._metrics.get(name, 0) + 1
                self._metrics["total_time"] += elapsed
                log.info("[%s]  %s  %.2fs", title[:50], name, elapsed)
                transcript = transcript.strip()
                self._cache.set(lecture, transcript)
                return {"video_title": title, "transcript": transcript, "strategy": name}

        elapsed = time.perf_counter() - t0
        self._metrics["FAILED"] += 1
        log.error("[%s]  ALL strategies failed  %.2fs", title[:50], elapsed)
        self._save_debug(lecture)
        return {"video_title": title, "transcript": "(No transcript found.)", "strategy": "FAILED"}

    def extract_api_parallel(
        self, lectures: list[dict], max_workers: int = 4
    ) -> list[Optional[str]]:
        """
        Parallel Level-2 API calls only (thread-safe, no Selenium).
        Returns list of transcripts or None; None = fall back to extract().
        # ponytail: max_workers=4 conservative; tune up if rate limits allow
        """
        http = self._http_session()
        if not http:
            return [None] * len(lectures)

        api = ApiExtractor()
        results: list[Optional[str]] = [None] * len(lectures)

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {
                ex.submit(api.extract, None, lec, http_session=http): i
                for i, lec in enumerate(lectures)
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except Exception as exc:
                    log.debug("Parallel API error: %s", exc)

        return results

    def print_summary(self) -> None:
        m = self._metrics
        _keys = ("CACHE", "VTT", "PLAYBACK_VTT", "NETWORK", "API",
                 "EMBEDDED_JSON", "DOM", "SELENIUM_FALLBACK", "FAILED")
        counts = {k: int(m.get(k, 0)) for k in _keys}
        total = sum(counts.values())
        if total == 0:
            return
        non_cache = total - counts["CACHE"]
        avg = float(m["total_time"]) / max(1, non_cache)
        labels = {
            "CACHE":             "Cache hits",
            "VTT":               "VTT track (passive)",
            "PLAYBACK_VTT":      "VTT after playback",
            "NETWORK":           "Network interception",
            "API":               "Direct API",
            "EMBEDDED_JSON":     "Embedded JSON",
            "DOM":               "DOM (no click)",
            "SELENIUM_FALLBACK": "Selenium fallback",
            "FAILED":            "Failed",
        }
        print("\n" + "=" * 44)
        print(f"  Extraction summary — {total} lectures")
        print("=" * 44)
        for key, label in labels.items():
            if counts.get(key):
                print(f"  {label:<24}: {counts[key]}")
        print(f"  {'Avg time (non-cache)':<24}: {avg:.2f}s/lecture")
        print("=" * 44 + "\n")

    def _wait_ready(self, driver: webdriver.Remote) -> None:
        try:
            WebDriverWait(driver, 15).until(
                lambda d: d.execute_script("return document.readyState") in ("complete", "interactive")
            )
        except TimeoutException:
            pass

    def _save_debug(self, lecture: dict) -> None:
        self.debug_dir.mkdir(exist_ok=True)
        label = _safe_filename(lecture.get("title", "unknown"))
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            (self.debug_dir / f"page_{label}_{ts}.html").write_text(
                self.driver.page_source, encoding="utf-8"
            )
            nw = self.driver.execute_script("return window._cr || []")
            (self.debug_dir / f"network_{label}_{ts}.json").write_text(
                json.dumps(nw, indent=2), encoding="utf-8"
            )
        except Exception:
            pass
        try:
            self.driver.save_screenshot(str(self.debug_dir / f"screenshot_{label}_{ts}.png"))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Minimal self-test (no browser required)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    assert _parse_lecture_id("https://www.coursera.org/learn/ml/lecture/abc123") == "abc123"
    assert _parse_lecture_id("https://www.coursera.org/learn/ml") is None

    # VTT deduplication: overlapping cues should not repeat
    vtt_dup = "WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.000\nHello world\n\n2\n00:00:01.500 --> 00:00:03.000\nHello world\n\n3\n00:00:03.000 --> 00:00:04.000\nGoodbye\n"
    parsed = _parse_vtt(vtt_dup)
    assert parsed.count("Hello world") == 1, f"expected 1 'Hello world', got: {parsed!r}"
    assert "Goodbye" in parsed

    # VTT inline tag stripping
    vtt_tags = "WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.000\n<c.colorE5E5E5>Clean text</c>\n"
    assert _parse_vtt(vtt_tags).strip() == "Clean text"

    data = {"transcript": "This is a test transcript with enough content to pass the length check here."}
    assert _search_transcript_in_json(data) is not None
    phrases = {"phrases": [{"text": "Hello"}, {"text": "World"}, {"text": "Done"}]}
    assert _search_transcript_in_json(phrases) is not None

    assert len(_BUTTON_SELECTORS) >= 7
    assert _BUTTON_SELECTORS[0][2] == "data-testid=item-tool-panel-button-transcript"

    # VTT is now Level 1
    from transcript_extractor import VTTExtractor, PlaybackVTTExtractor, NetworkExtractor
    assert VTTExtractor().name == "VTT"
    assert PlaybackVTTExtractor().name == "PLAYBACK_VTT"
    assert NetworkExtractor().name == "NETWORK"
    e = TranscriptExtractor.__new__(TranscriptExtractor)
    e._strategies = [VTTExtractor(), PlaybackVTTExtractor(), NetworkExtractor()]
    names = [s.name for s in e._strategies]
    assert names[0] == "VTT"
    assert names[1] == "PLAYBACK_VTT"
    assert names[2] == "NETWORK"

    print("All self-tests passed.")
