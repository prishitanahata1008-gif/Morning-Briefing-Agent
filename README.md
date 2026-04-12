# Morning Briefing Agent

A personal morning briefing agent that runs automatically every time you log into your Mac. It pulls live data from multiple APIs, generates AI-written content and news summaries, and delivers a beautifully styled briefing to your inbox and browser — before you've had your first coffee.

---

## What it does

Every morning on login, the agent:

1. **Fetches London weather** from [wttr.in](https://wttr.in) — temperature, feels-like, humidity, wind, and a 3-day forecast icon
2. **Fetches live currency rates** from [Frankfurter](https://frankfurter.app) — GBP, EUR, and USD each converted to INR
3. **Pulls top headlines** from [NewsAPI](https://newsapi.org) across BBC News, The Guardian, Reuters, and others
4. **Filters and summarises news with Gemini** — keeps only geopolitics and business stories, throws out sports and entertainment, and rewrites each story in 2–3 plain conversational sentences with a link to the full article
5. **Fetches your Google Calendar** events for today, tomorrow, and the day after — with automatic deadline detection
6. **Fetches your Google Tasks** incomplete to-do list, highlighting overdue items in red
7. **Generates a Word of the Day** using Gemini — a rare or evocative word with etymology, full definition, and a literary example sentence
8. **Generates a Fun Fact** using Gemini — at least 3 sentences of context and backstory, something genuinely worth sharing
9. **Saves a styled HTML briefing** (`briefing.html`) with a clean card-based layout
10. **Deploys the briefing to Netlify** so it has a permanent public URL you can open on any device
11. **Emails you the link** via Gmail so it's waiting in your inbox each morning
12. **Triggers a Mac desktop notification** that opens the briefing in Chrome on click

---

## APIs used

| Service | Purpose | Key required |
|---|---|---|
| [wttr.in](https://wttr.in) | Weather | No |
| [Frankfurter](https://frankfurter.app) | Currency exchange rates | No |
| [NewsAPI](https://newsapi.org) | Headlines | Yes (free tier) |
| [Google Gemini](https://ai.google.dev) | News summaries, word of the day, fun fact | Yes (free tier) |
| [Google Calendar API](https://developers.google.com/calendar) | Your calendar events | OAuth via credentials.json |
| [Google Tasks API](https://developers.google.com/tasks) | Your to-do list | OAuth via credentials.json |
| [Gmail API](https://developers.google.com/gmail) | Send notification email | OAuth via credentials.json |
| [Netlify](https://netlify.com) | Host briefing.html at a permanent public URL | Yes (personal access token) |

---

## Setup

### 1. Clone the repo

```bash
git clone <your-repo-url>
cd morning-briefing-agent
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
NETLIFY_TOKEN=your_netlify_personal_access_token
YOUR_EMAIL=your_gmail_address
```

### 4. Add your Google OAuth credentials

- Go to [Google Cloud Console](https://console.cloud.google.com)
- Create a project and enable: **Google Calendar API**, **Gmail API**, **Google Tasks API**
- Create an OAuth 2.0 client ID (Desktop app) and download the JSON
- Save it as `credentials.json` in the project folder

### 5. Run it

```bash
python3 morning_briefing.py
```

The first run will open a browser for Google sign-in. After you grant access, `token.json` is saved automatically and you won't be prompted again.

Your briefing will be at **`https://morning-briefing-[name].netlify.app`** (created on first run, same URL every day after).

### 6. Run automatically on login (macOS)

A `launchd` plist is included. To install:

```bash
cp com.prishita.morningbriefing.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.prishita.morningbriefing.plist
```

The agent will now run automatically every time you log into your Mac. Logs are written to `briefing.log` and `briefing_error.log` in the project folder.

---

## Project structure

```
├── morning_briefing.py        # Main agent script
├── requirements.txt           # Python dependencies
├── .env                       # Your secrets (not committed)
├── .env.example               # Template for .env
├── .gitignore
├── credentials.json           # Google OAuth client secret (not committed)
├── token.json                 # Google OAuth token, auto-generated (not committed)
├── netlify_site.json          # Netlify site ID, auto-generated (not committed)
├── briefing.html              # Generated daily briefing (not committed)
├── briefing.log               # stdout log
└── briefing_error.log         # stderr log
```

---

## Notes

- The Netlify site is created automatically on first run. The site ID is saved to `netlify_site.json` so the same URL is reused every morning.
- If you move the project folder, update the paths in the launchd plist and reload it.
- NewsAPI free tier limits requests to 100/day and headlines may have a short delay.
- Gemini free tier has per-minute and per-day rate limits — the script makes two calls per run (word/fact + news summaries).
