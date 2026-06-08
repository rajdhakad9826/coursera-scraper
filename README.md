# Coursera Transcript Scraper & PDF Generator

This script automates the process of scraping transcripts from Coursera courses and compiling them into a nicely formatted PDF file. It handles login, course navigation, transcript extraction, and PDF generation.

## Prerequisites

1. **Python 3.x**: Ensure you have Python installed.
2. **Google Chrome**: Google Chrome must be installed on your system, as this script uses Selenium to drive it.

## Installation

1. Clone or download this repository.
2. (Recommended) Activate your virtual environment if you have one. For example:
   ```bash
   source coursera-env/bin/activate
   ```
3. Install the required Python packages:

   ```bash
   pip install -r requirements.txt
   ```

## Usage

Before running the script, make sure your virtual environment is activated:

```bash
source coursera-env/bin/activate
```

Then run the script from your terminal:

```bash
python coursera_transcript_scraper.py
```

The script will prompt you for:
1. Your Coursera email address.
2. Your Coursera password (input is hidden).
3. The URL of the course you want to scrape (e.g., `https://www.coursera.org/learn/machine-learning`).

Alternatively, you can edit the configuration variables at the top of the `coursera_transcript_scraper.py` script to avoid being prompted each time:
```python
DEFAULT_EMAIL    = "you@gmail.com"
DEFAULT_PASSWORD = "yourpassword"
DEFAULT_COURSE_URL = "https://www.coursera.org/learn/machine-learning"
```

### How it works

1. **Login**: The script uses Selenium to log into your Coursera account. The browser opens visibly so you can complete any 2FA or CAPTCHA challenges if they appear.
2. **Navigation**: It automatically navigates through the course modules to collect all lecture links.
3. **Scraping**: It visits each lecture page and extracts the transcript text.
4. **PDF Generation**: It compiles all extracted transcripts into a formatted PDF file (default: `coursera_transcripts.pdf`).

### Resuming Progress

The script saves a checkpoint file (`coursera_transcripts_checkpoint.json`) after scraping each lecture. If the script is interrupted, running it again for the same course will automatically resume from the last saved checkpoint, skipping already scraped lectures.

### Headless Mode (Running in the Background)

By default, the script runs with a visible browser window. To run it in the background (headless mode), edit the `HEADLESS` variable in `coursera_transcript_scraper.py`:

```python
HEADLESS = True
```
*Note: Coursera's login page sometimes blocks headless browsers. It's recommended to keep it `False` on the first run; switch to `True` once you've successfully logged in and handled any potential 2FA.*

## Output

- `coursera_transcripts.pdf`: The final compiled PDF containing all scraped transcripts.
- `coursera_transcripts_checkpoint.json`: A temporary JSON file used for resuming progress.

## Disclaimer

This script is for personal use and educational purposes only. Please respect Coursera's terms of service and content copyrights.
