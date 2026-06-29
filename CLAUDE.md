# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```bash
python3 -m venv coursera-env && source coursera-env/bin/activate
pip install -r requirements.txt
pip install -e .
```

## Running

```bash
# CLI (preferred)
coursera-scraper scrape "machine-learning"
coursera-scraper scrape "https://www.coursera.org/learn/python" --browser firefox --headless

# Legacy interactive mode (still works)
python coursera_transcript_scraper.py

# Export transcripts from checkpoint (no re-scrape)
./export.sh
./export.sh path/to/checkpoint.json
./export.sh path/to/checkpoint.json output-dir

# Debug helpers
coursera-scraper browsers    # list detected browsers
coursera-scraper version
```

No test suite exists. Validate changes by running the scraper against a real course.

## Architecture

Five modules, clean separation:

| Module | Role |
|---|---|
| `cli.py` | Argparse entry point. Wires all modules together. |
| `browser_manager.py` | `BrowserManager` + `Browser` enum. Detects installed browsers, creates Selenium `WebDriver` with anti-bot flags. |
| `session_manager.py` | Persists login state. Chrome/Edge: `--user-data-dir` profile. Firefox: cookie JSON. Stored in `~/.coursera-scraper/profiles/<browser>/`. |
| `transcript_extractor.py` | 7-level cascade extractor (fastest → slowest). Each level is an `AbstractExtractor` subclass. Falls through to next on failure. |
| `coursera_transcript_scraper.py` | Course navigation (week/module links → lecture URLs), "reading"/"practice lab" title filter, checkpoint/resume, PDF generation via ReportLab. |

### Transcript extraction cascade (transcript_extractor.py)

1. `VTTExtractor` — passive `<track>` element → `subtitleAssetProxy` VTT download
2. `PlaybackVTTExtractor` — trigger playback, wait for lazy-loaded `<track>`
3. `NetworkExtractor` — CDP JS injection intercepts XHR/fetch matching `transcript|caption|subtitle|onDemandLecture`
4. `ApiExtractor` — Coursera REST API (`onDemandLectureTranscripts.v1`) via `requests` + browser cookies
5. `EmbeddedJsonExtractor` — `window.__APOLLO_STATE__` / `<script type=application/json>`
6. `DomExtractor` — transcript panel already in DOM
7. `SeleniumFallback` — click transcript button, wait, scrape panel

Transcripts are cached to `.cache/<md5_of_url>.txt` — delete to force re-fetch.

### Link collection and filtering (coursera_transcript_scraper.py)

`collect_module_links()` scrapes week/module navigation, then lecture URLs. Items whose title matches `\breading\b` or `\bpractice\s+lab\b` (case-insensitive) are skipped at collection time, before any extraction is attempted.

Checkpoint state (`coursera_transcripts_checkpoint.json`) persists after each lecture so interrupted runs resume without re-scraping.

### Browser anti-detection

`BrowserManager.create_driver()` sets `--disable-blink-features=AutomationControlled`, excludes automation switches, and patches `navigator.webdriver` via CDP — applies to Chrome, Chromium, Edge. Firefox has no equivalent CDP path.

## Key constants

`WAIT_TIMEOUT = 20` (seconds) in `coursera_transcript_scraper.py` — raise if Coursera pages load slowly.
