# Coursera Transcript Scraper & PDF Generator

Scrapes all lecture transcripts from a Coursera course and compiles them into a single searchable PDF. Built for personal study workflows — review course content or feed it into AI note-taking tools.

## Prerequisites

- Python 3.10+
- At least one of: **Chrome**, **Edge**, **Chromium**, or **Firefox**

No manual driver downloads needed. Selenium 4.6+ manages drivers automatically.

## Installation

```bash
git clone https://github.com/rajdhakad9826/coursera-scraper.git
cd coursera-scraper

python3 -m venv coursera-env
source coursera-env/bin/activate        # Windows: coursera-env\Scripts\activate

pip install -r requirements.txt
pip install -e .                        # installs the coursera-scraper command
```

## CLI Usage

### Scrape a course

```bash
# by course slug (URL: https://www.coursera.org/learn/machine-learning)
coursera-scraper scrape "machine-learning"

# by full URL
coursera-scraper scrape "https://www.coursera.org/learn/python"

# spaces in name are auto-slugified
coursera-scraper scrape "machine learning"
```

### Choose a browser

```bash
coursera-scraper scrape "python" --browser chrome
coursera-scraper scrape "python" --browser firefox
coursera-scraper scrape "python" --browser edge
coursera-scraper scrape "python" --browser chromium
```

### Headless mode

```bash
coursera-scraper scrape "python" --headless
```

> **Note:** headless mode skips the manual login prompt. Only use this if your browser session is already authenticated (e.g. persistent profile with saved cookies).

### List detected browsers

```bash
coursera-scraper browsers
```

Output:
```
Available browsers:

  ✓ chrome
  ✓ edge
  ✗ firefox
  ✓ chromium
```

### Version

```bash
coursera-scraper version
```

## Browser Auto-Detection

When `--browser` is not specified, the scraper detects installed browsers and picks the best one automatically:

**Priority:** Chrome → Edge → Chromium → Firefox

```
Detected browser: chrome
```

If the requested browser is not installed:
```
Error: requested browser firefox not found.

Available browsers:
  - chrome
  - edge
```

If no browser is found:
```
No supported browser found.

Install one of:
  - chrome
  - edge
  - chromium
  - firefox
```

## Login

The scraper opens a browser window and navigates to the Coursera login page. Log in manually (handles 2FA, CAPTCHA, SSO), then press **ENTER** in the terminal. The scraper continues automatically from there.

## Checkpoint / Resume

After each lecture, progress is saved to `coursera_transcripts_checkpoint.json`. If interrupted, re-run the same command — already-scraped lectures are skipped automatically.

## Output

- `coursera_transcripts.pdf` — formatted PDF with all transcripts
- `coursera_transcripts_checkpoint.json` — resume state (safe to delete after completion)

## Export

After scraping, convert the checkpoint into structured transcript files without re-scraping:

```bash
# default: reads coursera_transcripts_checkpoint.json, writes to exports/
./export.sh

# custom checkpoint path
./export.sh path/to/checkpoint.json

# custom checkpoint + output directory
./export.sh path/to/checkpoint.json my-output-dir
```

### Output structure

```
exports/
└── <course-slug>/
    ├── transcript.txt      ← all lectures merged, plain text
    ├── transcript.md       ← all lectures merged, markdown with headings
    ├── metadata.json       ← index of all lectures with filenames
    └── transcripts/
        ├── 001-introduction.txt
        ├── 001-introduction.md
        ├── 002-week-1-overview.txt
        ├── 002-week-1-overview.md
        └── ...
```

### Export is idempotent

Running `./export.sh` multiple times overwrites the output files. The checkpoint JSON is never modified.

### Common errors

**`checkpoint not found`** — the scraper hasn't run yet, or the checkpoint is in a different directory. Pass the path explicitly: `./export.sh path/to/coursera_transcripts_checkpoint.json`

**`checkpoint is not valid JSON`** — the checkpoint file is corrupted or truncated. Re-run the scraper to regenerate it.

**`output directory is not writable`** — check permissions on the output directory.

## Migrating from v1.0 (direct script usage)

Old:
```bash
python coursera_transcript_scraper.py
# prompted for URL, always used Chrome
```

New (equivalent):
```bash
coursera-scraper scrape "https://www.coursera.org/learn/your-course"
```

Direct script execution still works unchanged for backwards compatibility:
```bash
python coursera_transcript_scraper.py
```

## Troubleshooting

**`coursera-scraper: command not found`** — run `pip install -e .` in the project root.

**Browser not detected** — run `coursera-scraper browsers` to see what's found. Install Chrome or run with `--browser` pointing to an installed browser.

**Session expired mid-scrape** — the checkpoint saves after each lecture. Re-run the same command to resume.

## Disclaimer

For personal use and educational purposes only. Respect Coursera's terms of service and content copyrights. Not for redistribution.
