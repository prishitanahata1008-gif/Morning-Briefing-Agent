# Jarvis — Morning Briefing Agent

A personal morning briefing agent that runs automatically every time you open your Mac. It pulls live data from multiple APIs, generates AI-written content and news summaries, and delivers a styled briefing to your inbox and browser.

---

## What it does

Every morning on wake, the agent:

1. **Fetches London weather** — temperature, feels-like, humidity, wind
2. **Fetches live currency rates** — GBP, EUR, and USD each converted to INR
3. **Fetches live gold price** — spot price per 10g in INR, per troy oz in USD, per gram in GBP
4. **Pulls top headlines** from NewsAPI across US, UK, and business categories
5. **Filters and summarises news with Gemini** — keeps only geopolitics and business, rewrites each story in plain conversational sentences
6. **Fetches your Google Calendar** — events for today, tomorrow, and the day after with automatic deadline detection
7. **Fetches your Google Tasks** — incomplete to-do list with overdue highlighting
8. **Generates a Word of the Day** using Gemini — a rare or evocative word with definition and example sentence
9. **Generates an educational fact** using Gemini — counterintuitive, 2–3 sentences, must have a clear takeaway
10. **Deploys the briefing to Vercel** so it has a permanent public URL
11. **Emails you the link** via Gmail
12. **Triggers a Mac desktop notification** that opens the briefing in Chrome

---

## APIs used

| Service | Purpose | Key required |
|---|---|---|
| [wttr.in](https://wttr.in) | Weather | No |
| [Frankfurter](https://frankfurter.app) | Currency exchange rates | No |
| [Yahoo Finance](https://finance.yahoo.com) | Gold spot price | No |
| [NewsAPI](https://newsapi.org) | Headlines | Yes — free tier |
| [Google Gemini](https://ai.google.dev) | News summaries, word of the day, fact | Yes — free tier |
| [Google Calendar API](https://developers.google.com/calendar) | Your calendar events | OAuth |
| [Google Tasks API](https://developers.google.com/tasks) | Your to-do list | OAuth |
| [Gmail API](https://developers.google.com/gmail) | Send notification email | OAuth |
| [Vercel](https://vercel.com) | Host briefing at a permanent URL | Yes — free tier |

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/prishitanahata1008-gif/Morning-Briefing-Agent
cd Morning-Briefing-Agent
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Add your API keys

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

```
NEWS_API_KEY=your_newsapi_key
GEMINI_API_KEY=your_gemini_api_key
VERCEL_TOKEN=your_vercel_token
YOUR_EMAIL=your_gmail_address
```

- **NewsAPI** — free key at [newsapi.org](https://newsapi.org)
- **Gemini** — free key at [ai.google.dev](https://ai.google.dev)
- **Vercel** — create a token at vercel.com → Settings → Tokens

### 4. Set up Google OAuth

- Go to [Google Cloud Console](https://console.cloud.google.com)
- Create a project and enable: **Google Calendar API**, **Gmail API**, **Google Tasks API**
- Go to OAuth consent screen → set to **External** → publish the app (so the token doesn't expire every 7 days)
- Create an **OAuth 2.0 client ID** (Desktop app) and download the JSON
- Save it as `credentials.json` in the project folder

### 5. Run it

```bash
python3 morning_briefing.py
```

The first run opens a browser for Google sign-in. After you grant access, `token.json` is saved automatically and you won't be prompted again.

Your briefing deploys to **`https://morning-briefing-[yourname].vercel.app`**.

### 6. Run automatically on Mac wake (macOS only)

The agent uses **sleepwatcher** to trigger on laptop wake rather than login, so it fires when you open your lid in the morning.

**Install sleepwatcher:**
```bash
brew install sleepwatcher
brew services start sleepwatcher
```

**Create the wake hook** at `~/.wakeup`:
```bash
#!/bin/bash
echo "$(date): wake event triggered" >> /tmp/wakeup_test.log
/bin/bash "/path/to/Morning-Briefing-Agent/run_briefing.sh"
```
```bash
chmod +x ~/.wakeup
```

**`run_briefing.sh`** (included in the repo) handles:
- Only running between 6am–2pm
- Only running once per day (stamp file in `/tmp`)
- Logging to `briefing.log` / `briefing_error.log`

Update the paths inside `run_briefing.sh` to match where you cloned the repo.

---

## Project structure

```
├── morning_briefing.py        # Main agent script
├── run_briefing.sh            # Wake trigger wrapper (called by sleepwatcher)
├── requirements.txt           # Python dependencies
├── .env.example               # Template — copy to .env and fill in keys
├── .gitignore
├── credentials.json           # Google OAuth client secret (not committed)
├── token.json                 # Google OAuth token, auto-generated (not committed)
├── briefing.html              # Generated daily briefing (not committed)
├── briefing.log               # stdout log (not committed)
└── briefing_error.log         # stderr log (not committed)
```

---

## Notes

- If you move the project folder, update the paths in `run_briefing.sh` and `~/.wakeup`
- NewsAPI free tier: 100 requests/day, headlines may have a short delay
- Gemini free tier: the script makes two calls per run (word/fact + news summaries) with a 15s gap between them to avoid rate limits
- Gold price is fetched from Yahoo Finance's public endpoint — no API key needed
- The Vercel project is created automatically on first run and reuses the same URL every day
