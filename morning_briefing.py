#!/usr/bin/env python3
"""
Morning Briefing Agent
Fetches weather, news, calendar events, and AI-generated content,
then saves a styled HTML briefing and emails a notification.
"""

import os
import re
import io
import json
import base64
import zipfile
import datetime
import requests
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from google import genai

load_dotenv()

# ── CONFIGURATION ─────────────────────────────────────────────────────────────
NEWS_API_KEY    = os.environ["NEWS_API_KEY"]
GEMINI_API_KEY  = os.environ["GEMINI_API_KEY"]
NETLIFY_TOKEN   = os.environ["NETLIFY_TOKEN"]
YOUR_EMAIL      = os.environ["YOUR_EMAIL"]
WEATHER_CITY    = "London"

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/tasks.readonly",
]

BASE_DIR          = Path(__file__).parent
CREDENTIALS_FILE  = BASE_DIR / "credentials.json"
TOKEN_FILE        = BASE_DIR / "token.json"
OUTPUT_FILE       = BASE_DIR / "briefing.html"
NETLIFY_SITE_FILE = BASE_DIR / "netlify_site.json"
# ─────────────────────────────────────────────────────────────────────────────


# ── GOOGLE AUTH ───────────────────────────────────────────────────────────────

def get_google_creds() -> Credentials:
    """Load, refresh, or create Google OAuth credentials."""
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
    return creds


# ── WEATHER ───────────────────────────────────────────────────────────────────

WEATHER_ICONS = {
    ("sun", "clear"):              "☀️",
    ("partly", "cloud"):           "⛅",
    ("cloud", "overcast"):         "☁️",
    ("rain", "drizzle", "shower"): "🌧️",
    ("thunder", "storm"):          "⛈️",
    ("snow", "sleet", "blizzard"): "❄️",
    ("fog", "mist", "haze"):       "🌫️",
}

def _weather_icon(description: str) -> str:
    d = description.lower()
    for keywords, icon in WEATHER_ICONS.items():
        if any(k in d for k in keywords):
            return icon
    return "🌡️"


def fetch_weather() -> dict | None:
    try:
        r = requests.get(f"https://wttr.in/{WEATHER_CITY}?format=j1", timeout=10)
        r.raise_for_status()
        data    = r.json()
        current = data["current_condition"][0]
        today   = data["weather"][0]
        desc    = current["weatherDesc"][0]["value"]
        return {
            "temp_c":       current["temp_C"],
            "feels_like_c": current["FeelsLikeC"],
            "description":  desc,
            "humidity":     current["humidity"],
            "wind_kmph":    current["windspeedKmph"],
            "max_c":        today["maxtempC"],
            "min_c":        today["mintempC"],
            "icon":         _weather_icon(desc),
        }
    except Exception as e:
        print(f"  [Weather] Error: {e}")
        return None


# ── NEWS ──────────────────────────────────────────────────────────────────────

def fetch_news() -> list[dict]:
    """Fetch a larger pool of headlines — Gemini will filter and summarise them."""
    try:
        r = requests.get(
            "https://newsapi.org/v2/top-headlines",
            params={
                "sources": "bbc-news,the-guardian-uk,reuters,the-times,independent",
                "pageSize": 15,
                "apiKey": NEWS_API_KEY,
            },
            timeout=10,
        )
        r.raise_for_status()
        return [
            {
                "title":       a.get("title", ""),
                "source":      a.get("source", {}).get("name", ""),
                "url":         a.get("url", "#"),
                "description": (a.get("description") or ""),
            }
            for a in r.json().get("articles", [])
        ]
    except Exception as e:
        print(f"  [News] Error: {e}")
        return []


# ── CALENDAR ──────────────────────────────────────────────────────────────────

def _fmt_time(dt_str: str) -> str:
    if not dt_str:
        return ""
    try:
        if "T" in dt_str:
            dt = datetime.datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            return dt.strftime("%-I:%M %p")
        return "All day"
    except Exception:
        return dt_str


_DEADLINE_KEYWORDS = {"deadline", "due", "submit", "submission", "hand in", "handover", "delivery", "final", "last day", "expires", "expiry"}

def _is_deadline(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in _DEADLINE_KEYWORDS)


def fetch_calendar_events(creds: Credentials) -> dict:
    """Return events for today + next 2 days, grouped by date label."""
    try:
        service = build("calendar", "v3", credentials=creds)
        today   = datetime.date.today()

        days = []
        for offset in range(3):
            day = today + datetime.timedelta(days=offset)
            if offset == 0:
                label = "Today"
            elif offset == 1:
                label = "Tomorrow"
            else:
                label = day.strftime("%A, %b %-d")

            start = datetime.datetime.combine(day, datetime.time.min).isoformat() + "Z"
            end   = datetime.datetime.combine(day, datetime.time.max).isoformat() + "Z"

            result = (
                service.events()
                .list(
                    calendarId="primary",
                    timeMin=start,
                    timeMax=end,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )

            events = []
            for e in result.get("items", []):
                start_dt = e["start"].get("dateTime", e["start"].get("date", ""))
                end_dt   = e["end"].get("dateTime",   e["end"].get("date",   ""))
                title    = e.get("summary", "(No title)")
                events.append({
                    "summary":    title,
                    "start":      _fmt_time(start_dt),
                    "end":        _fmt_time(end_dt),
                    "location":   e.get("location", ""),
                    "is_deadline": _is_deadline(title),
                })
            days.append({"label": label, "date": day.strftime("%b %-d"), "events": events})

        return days
    except Exception as e:
        print(f"  [Calendar] Error: {e}")
        return []


# ── CURRENCY ─────────────────────────────────────────────────────────────────

def fetch_currency_rates() -> dict | None:
    """Fetch how many INR you get for 1 GBP, 1 EUR, and 1 USD."""
    try:
        r = requests.get(
            "https://api.frankfurter.app/latest",
            params={"from": "INR", "to": "GBP,EUR,USD"},
            timeout=10,
        )
        r.raise_for_status()
        rates = r.json().get("rates", {})
        # rates give 1 INR → X foreign. Invert to get 1 foreign → Y INR
        return {
            "GBP": round(1 / rates["GBP"], 2) if rates.get("GBP") else None,
            "EUR": round(1 / rates["EUR"], 2) if rates.get("EUR") else None,
            "USD": round(1 / rates["USD"], 2) if rates.get("USD") else None,
        }
    except Exception as e:
        print(f"  [Currency] Error: {e}")
        return None


# ── TASKS ─────────────────────────────────────────────────────────────────────

def fetch_tasks(creds: Credentials) -> list[dict]:
    try:
        service   = build("tasks", "v1", credentials=creds)
        lists_res = service.tasklists().list(maxResults=10).execute()
        all_tasks = []
        for tl in lists_res.get("items", []):
            tasks_res = service.tasks().list(
                tasklist=tl["id"],
                showCompleted=False,
                showHidden=False,
            ).execute()
            for t in tasks_res.get("items", []):
                if t.get("status") == "needsAction":
                    due = t.get("due", "")
                    all_tasks.append({
                        "title":    t.get("title", "(Untitled)"),
                        "due":      _parse_task_due(due),
                        "overdue":  _is_overdue(due),
                        "list":     tl.get("title", ""),
                        "notes":    t.get("notes", ""),
                    })
        return all_tasks
    except Exception as e:
        print(f"  [Tasks] Error: {e}")
        return []


def _parse_task_due(due_str: str) -> str:
    if not due_str:
        return ""
    try:
        dt = datetime.datetime.fromisoformat(due_str.replace("Z", "+00:00"))
        return dt.strftime("%a, %b %-d")
    except Exception:
        return ""


def _is_overdue(due_str: str) -> bool:
    if not due_str:
        return False
    try:
        dt  = datetime.datetime.fromisoformat(due_str.replace("Z", "+00:00"))
        return dt.date() < datetime.date.today()
    except Exception:
        return False


# ── GEMINI ────────────────────────────────────────────────────────────────────

_GEMINI_FALLBACK = {
    "word":           "Sonder",
    "pronunciation":  "/ˈsɒn.dər/",
    "part_of_speech": "noun",
    "etymology":      "Coined by John Koenig in The Dictionary of Obscure Sorrows (2012), blending the French 'sonder' (to probe/fathom) with a sense of sudden depth.",
    "definition":     "The profound, unsettling realisation that every passerby is living a life as vivid, complex, and full of quiet drama as your own — complete with ambitions, fears, routines, and a cast of people who matter deeply to them, none of which you will ever know.",
    "example":        "Stuck in traffic, she was seized by sonder — every car around her a tiny theatre of someone else's Monday, someone else's crisis, someone else's song playing too loud.",
    "fun_fact":       "The Eiffel Tower grows taller in summer. Due to thermal expansion, the iron structure can grow by up to 15 centimetres on hot days as the metal heats up and its atoms move further apart. This means the exact height of one of the world's most visited landmarks is technically a moving target — Gustave Eiffel's tower is, in a quiet way, alive to the seasons.",
}

def _call_gemini(prompt: str) -> str:
    """Single entry point for all Gemini calls using the current SDK."""
    client   = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
    return response.text.strip()


def _strip_fences(text: str) -> str:
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def fetch_gemini_content() -> dict:
    today = datetime.date.today().strftime("%A, %B %-d, %Y")
    try:
        text = _call_gemini(
            f"Today is {today}. Use this date to ensure your response is unique — different word and fact every day.\n\n"
            "Give me TWO things:\n\n"
            "1. WORD OF THE DAY — Choose a rare, evocative, or philosophically rich English word that most educated "
            "people have never used. Avoid common words. Include:\n"
            "   - The word itself\n"
            "   - IPA pronunciation\n"
            "   - Part of speech\n"
            "   - Etymology: where it comes from, the original language and root meaning, how it entered English — 2 sentences\n"
            "   - Definition: rich and precise, capturing every nuance — not a dictionary stub\n"
            "   - Example: one vivid, literary sentence where the word fits so naturally its meaning is unmistakable\n\n"
            "2. FUN FACT — Surprising, counterintuitive, or mind-expanding. Rules:\n"
            "   - At least 3 sentences with context and backstory — a satisfying short read, not a trivia card\n"
            "   - Cover science, history, nature, psychology, geography, or culture — NOT sports or celebrity\n\n"
            "Respond ONLY with valid JSON, no markdown fences:\n"
            '{"word":"...","pronunciation":"...","part_of_speech":"...","etymology":"...","definition":"...","example":"...","fun_fact":"..."}'
        )
        return json.loads(_strip_fences(text))
    except Exception as e:
        print(f"  [Gemini] Error: {e} — using fallback content.")
        return _GEMINI_FALLBACK


def summarize_news_with_gemini(articles: list[dict]) -> list[dict]:
    """Filter to geopolitics + business only and write conversational summaries."""
    if not articles:
        return []

    url_map        = {a["title"]: a["url"] for a in articles}
    headlines_text = "\n".join(
        f"{i+1}. [{a['source']}] {a['title']} | {a['description'][:120]}"
        for i, a in enumerate(articles)
    )

    try:
        text = _call_gemini(
            "You are briefing a friend on the morning news. Here are today's headlines:\n\n"
            f"{headlines_text}\n\n"
            "STEP 1 — FILTER ruthlessly. Keep ONLY stories about:\n"
            "  • World news and geopolitics (wars, elections, diplomacy, international relations)\n"
            "  • Business and finance (markets, economy, major companies, trade)\n"
            "Discard everything else: sports, entertainment, celebrity, lifestyle, technology, science, TV, health.\n\n"
            "STEP 2 — From what remains, pick the 5 most significant stories.\n\n"
            "STEP 3 — For each story write a summary: 2-3 plain conversational sentences, like a smart friend "
            "telling you what happened and why it matters. No jargon. No filler like 'In a significant development'. "
            "Just: what happened, and what it means.\n\n"
            "Respond ONLY with a valid JSON array, no markdown fences:\n"
            '[{"title":"...","source":"...","url":"...","summary":"..."}]\n\n'
            "Copy title, source, and url exactly from the input above. Write only the summary yourself."
        )
        summarized = json.loads(_strip_fences(text))
        for s in summarized:
            if not s.get("url") or s["url"] in ("...", ""):
                s["url"] = url_map.get(s.get("title", ""), "#")
        return summarized
    except Exception as e:
        print(f"  [Gemini News] Error: {e} — showing raw headlines.")
        return [{"title": a["title"], "source": a["source"], "url": a["url"],
                 "summary": a.get("description", "")} for a in articles[:5]]


# ── NETLIFY ───────────────────────────────────────────────────────────────────

def upload_to_netlify(html_content: str) -> str:
    """Deploy briefing.html to Netlify. Creates the site on first run,
    then redeploys to the same URL every subsequent morning."""
    headers = {
        "Authorization": f"Bearer {NETLIFY_TOKEN}",
        "Content-Type":  "application/zip",
    }

    # Load existing site ID, or create a new site
    if NETLIFY_SITE_FILE.exists():
        config   = json.loads(NETLIFY_SITE_FILE.read_text())
        site_id  = config["site_id"]
        site_url = config["site_url"]
    else:
        print("  First run — creating Netlify site…")
        r = requests.post(
            "https://api.netlify.com/api/v1/sites",
            headers={"Authorization": f"Bearer {NETLIFY_TOKEN}"},
            json={"name": "morning-briefing-prishita"},
        )
        if r.status_code == 422:
            # Name already taken — let Netlify auto-generate one
            r = requests.post(
                "https://api.netlify.com/api/v1/sites",
                headers={"Authorization": f"Bearer {NETLIFY_TOKEN}"},
                json={},
            )
        r.raise_for_status()
        data     = r.json()
        site_id  = data["id"]
        site_url = data.get("ssl_url") or data.get("url")
        NETLIFY_SITE_FILE.write_text(json.dumps({"site_id": site_id, "site_url": site_url}))
        print(f"  Site created → {site_url}")

    # Pack index.html into a zip and deploy
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.html", html_content)
        zf.writestr("_headers", "/*\n  Content-Type: text/html; charset=utf-8\n")
    buf.seek(0)

    r = requests.post(
        f"https://api.netlify.com/api/v1/sites/{site_id}/deploys",
        headers=headers,
        data=buf.read(),
    )
    r.raise_for_status()
    print(f"  Deployed → {site_url}")
    return site_url


# ── EMAIL ─────────────────────────────────────────────────────────────────────

def send_email(creds: Credentials, briefing_url: str) -> None:
    try:
        service = build("gmail", "v1", credentials=creds)

        msg            = MIMEMultipart("alternative")
        msg["Subject"] = "☀️ Your Morning Briefing is Ready"
        msg["From"]    = YOUR_EMAIL
        msg["To"]      = YOUR_EMAIL

        plain = f"Good morning!\n\nYour briefing is ready:\n\n{briefing_url}\n\nHave a great day!"
        html  = f"""<p>Good morning!</p>
<p>Your morning briefing is ready:</p>
<p><a href="{briefing_url}" style="font-size:16px;font-weight:bold;">☀️ Open Morning Briefing</a></p>
<p>Have a great day!</p>"""

        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(html,  "html"))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        print(f"  Email sent to {YOUR_EMAIL}")
    except Exception as e:
        print(f"  [Email] Error: {e}")


# ── HTML GENERATION ───────────────────────────────────────────────────────────

def _build_weather_html(weather: dict | None) -> str:
    if not weather:
        return "<p class='empty'>Weather unavailable.</p>"
    return f"""
        <div class="weather-main">
            <span class="weather-icon">{weather['icon']}</span>
            <div>
                <div class="temp">{weather['temp_c']}°C</div>
                <div class="desc">{weather['description']}</div>
            </div>
        </div>
        <div class="weather-meta">
            <div class="meta-item"><span>Feels like</span><strong>{weather['feels_like_c']}°C</strong></div>
            <div class="meta-item"><span>High / Low</span><strong>{weather['max_c']}° / {weather['min_c']}°</strong></div>
            <div class="meta-item"><span>Humidity</span><strong>{weather['humidity']}%</strong></div>
            <div class="meta-item"><span>Wind</span><strong>{weather['wind_kmph']} km/h</strong></div>
        </div>"""


def _build_news_html(news: list[dict]) -> str:
    if not news:
        return "<p class='empty'>No headlines available.</p>"
    items = ""
    for i, a in enumerate(news, 1):
        summary = a.get("summary") or a.get("description", "")
        url     = a.get("url", "#")
        items += f"""
            <div class="news-item">
                <div class="news-num">{i:02d}</div>
                <div class="news-body">
                    <a href="{url}" target="_blank" class="news-title">{a['title']}</a>
                    <div class="news-source">{a['source']}</div>
                    {"<p class='news-desc'>" + summary + "</p>" if summary else ""}
                    <a href="{url}" target="_blank" class="read-more">Read full article →</a>
                </div>
            </div>"""
    return items


def _build_calendar_html(days: list[dict]) -> str:
    if not days:
        return "<p class='empty'>No events found.</p>"
    html = ""
    for day in days:
        html += f'<div class="cal-day-label">{day["label"]} <span class="cal-day-date">{day["date"]}</span></div>'
        if not day["events"]:
            html += "<p class='cal-empty'>Nothing scheduled.</p>"
        else:
            for e in day["events"]:
                end_part  = f" – {e['end']}" if e["end"] and e["end"] != e["start"] else ""
                loc       = f"<span class='event-loc'>📍 {e['location']}</span>" if e["location"] else ""
                deadline  = "<span class='deadline-badge'>⚠ Deadline</span>" if e["is_deadline"] else ""
                css_extra = " event-deadline" if e["is_deadline"] else ""
                html += f"""
                <div class="event-item{css_extra}">
                    <div class="event-time">{e['start']}{end_part}</div>
                    <div class="event-info">
                        <div class="event-title">{e['summary']} {deadline}</div>
                        {loc}
                    </div>
                </div>"""
    return html


def _build_currency_html(rates: dict | None) -> str:
    if not rates:
        return "<p class='empty'>Rates unavailable.</p>"
    entries = [
        ("🇬🇧", "GBP", "1 British Pound"),
        ("🇪🇺", "EUR", "1 Euro"),
        ("🇺🇸", "USD", "1 US Dollar"),
    ]
    items = ""
    for flag, code, label in entries:
        rate = rates.get(code)
        if rate is not None:
            items += f"""
            <div class="fx-item">
                <span class="fx-flag">{flag}</span>
                <div class="fx-label-group">
                    <span class="fx-currency">{label}</span>
                    <span class="fx-equals">equals</span>
                </div>
                <span class="fx-rate">₹{rate:,.2f}</span>
            </div>"""
    return f'<div class="fx-grid">{items}</div>'


def _build_tasks_html(tasks: list[dict]) -> str:
    if not tasks:
        return "<p class='empty'>No pending tasks. You're all caught up!</p>"
    items = ""
    for t in tasks:
        due_html  = f"<span class='task-due'>Due {t['due']}</span>" if t["due"] else ""
        overdue   = "<span class='task-overdue'>Overdue</span>" if t["overdue"] else ""
        notes_html = f"<p class='task-notes'>{t['notes']}</p>" if t["notes"] else ""
        items += f"""
        <div class="task-item{'  task-is-overdue' if t['overdue'] else ''}">
            <span class="task-check">○</span>
            <div class="task-body">
                <div class="task-title">{t['title']} {overdue}</div>
                {due_html}{notes_html}
            </div>
        </div>"""
    return items


def _build_word_html(g: dict) -> str:
    etymology = f"<p class='word-etymology'>{g['etymology']}</p>" if g.get("etymology") else ""
    return f"""
        <div class="word-header">
            <span class="word-text">{g.get('word', '')}</span>
            <span class="word-pos">{g.get('part_of_speech', '')}</span>
            <span class="word-pron">{g.get('pronunciation', '')}</span>
        </div>
        {etymology}
        <p class="word-def">{g.get('definition', '')}</p>
        <p class="word-ex">"{g.get('example', '')}"</p>"""


def generate_html(weather, news, events, gemini, rates, tasks) -> str:
    today          = datetime.date.today().strftime("%A, %B %-d, %Y")
    weather_html   = _build_weather_html(weather)
    news_html      = _build_news_html(news)
    calendar_html  = _build_calendar_html(events)
    word_html      = _build_word_html(gemini)
    fun_fact       = gemini.get("fun_fact", "")
    currency_html  = _build_currency_html(rates)
    tasks_html     = _build_tasks_html(tasks)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Morning Briefing — {today}</title>
<style>
  :root {{
    --bg:      #f0f2f5;
    --card:    #ffffff;
    --indigo:  #4f46e5;
    --sky:     #0ea5e9;
    --emerald: #10b981;
    --amber:   #f59e0b;
    --pink:    #ec4899;
    --text:    #1e293b;
    --muted:   #64748b;
    --border:  #e2e8f0;
    --shadow:  0 2px 14px rgba(0,0,0,.07);
    --r:       16px;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    padding: 36px 16px 72px;
  }}
  .wrap {{ max-width: 880px; margin: 0 auto; }}

  /* Header */
  header {{
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 32px;
  }}
  header h1 {{ font-size: 2.1rem; font-weight: 800; letter-spacing: -.02em; }}
  header .date {{ color: var(--muted); font-size: .95rem; }}

  /* Grid */
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
  @media (max-width: 620px) {{ .grid {{ grid-template-columns: 1fr; }} }}
  .full {{ grid-column: 1 / -1; }}

  /* Card */
  .card {{
    background: var(--card);
    border-radius: var(--r);
    box-shadow: var(--shadow);
    padding: 22px 24px;
    border-top: 4px solid var(--indigo);
  }}
  .card.weather  {{ border-color: var(--sky);     }}
  .card.news     {{ border-color: var(--indigo);  }}
  .card.calendar {{ border-color: var(--emerald); }}
  .card.word     {{ border-color: var(--amber);   }}
  .card.fact     {{ border-color: var(--pink);    }}
  .card.currency {{ border-color: #06b6d4;        }}
  .card.tasks    {{ border-color: #8b5cf6;        }}

  .section-label {{
    font-size: .68rem;
    font-weight: 700;
    letter-spacing: .1em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 16px;
  }}

  /* ── Weather ── */
  .weather-main {{ display: flex; align-items: center; gap: 16px; margin-bottom: 16px; }}
  .weather-icon {{ font-size: 3.6rem; line-height: 1; }}
  .temp  {{ font-size: 2.6rem; font-weight: 800; line-height: 1; }}
  .desc  {{ color: var(--muted); font-size: .95rem; margin-top: 4px; text-transform: capitalize; }}
  .weather-meta {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
  .meta-item {{
    background: var(--bg);
    border-radius: 10px;
    padding: 10px 12px;
    display: flex; flex-direction: column;
  }}
  .meta-item span   {{ font-size: .7rem; color: var(--muted); margin-bottom: 2px; }}
  .meta-item strong {{ font-size: .98rem; }}

  /* ── News ── */
  .news-item {{
    display: flex;
    gap: 14px;
    padding: 13px 0;
    border-bottom: 1px solid var(--border);
  }}
  .news-item:last-child {{ border-bottom: none; padding-bottom: 0; }}
  .news-num   {{ font-size: .72rem; font-weight: 700; color: var(--muted); min-width: 22px; padding-top: 3px; }}
  .news-title {{
    font-size: .88rem;
    font-weight: 600;
    color: var(--text);
    text-decoration: none;
    line-height: 1.45;
    display: block;
  }}
  .news-title:hover {{ color: var(--indigo); text-decoration: underline; }}
  .news-source {{ font-size: .7rem; color: var(--muted); margin-top: 4px; }}
  .news-desc   {{ font-size: .78rem; color: var(--muted); margin-top: 5px; line-height: 1.5; }}
  .read-more   {{ font-size: .72rem; font-weight: 600; color: var(--indigo); text-decoration: none; display: inline-block; margin-top: 6px; }}
  .read-more:hover {{ text-decoration: underline; }}

  /* ── Calendar ── */
  .card.calendar {{ border-color: var(--emerald); }}
  .cal-day-label {{
    font-size: .72rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: .08em; color: var(--emerald);
    margin: 18px 0 8px; padding-bottom: 6px;
    border-bottom: 2px solid var(--emerald);
    display: flex; align-items: center; gap: 8px;
  }}
  .cal-day-label:first-child {{ margin-top: 0; }}
  .cal-day-date {{ font-weight: 400; color: var(--muted); text-transform: none; letter-spacing: 0; }}
  .cal-empty {{ font-size: .82rem; color: var(--muted); font-style: italic; margin: 6px 0 10px 0; }}
  .event-item {{
    display: flex;
    gap: 14px;
    padding: 10px 0;
    border-bottom: 1px solid var(--border);
  }}
  .event-item:last-child {{ border-bottom: none; padding-bottom: 0; }}
  .event-item.event-deadline {{ background: #fffbeb; margin: 0 -8px; padding: 10px 8px; border-radius: 8px; border-bottom: none; }}
  .event-time  {{
    font-size: .73rem; font-weight: 700; color: var(--emerald);
    min-width: 96px; padding-top: 2px; white-space: nowrap;
  }}
  .event-title {{ font-size: .9rem; font-weight: 600; line-height: 1.4; }}
  .event-loc   {{ font-size: .72rem; color: var(--muted); margin-top: 3px; display: block; }}
  .deadline-badge {{
    font-size: .65rem; font-weight: 700; background: #fef3c7; color: #92400e;
    border: 1px solid #fcd34d; padding: 1px 7px; border-radius: 20px;
    vertical-align: middle; margin-left: 6px;
  }}

  /* ── Currency ── */
  .card.currency {{ border-color: #06b6d4; }}
  .fx-grid   {{ display: flex; flex-direction: column; gap: 8px; }}
  .fx-item   {{
    display: flex; align-items: center; gap: 12px;
    background: var(--bg); border-radius: 10px; padding: 10px 14px;
  }}
  .fx-flag        {{ font-size: 1.4rem; line-height: 1; flex-shrink: 0; }}
  .fx-label-group {{ display: flex; flex-direction: column; flex: 1; }}
  .fx-currency    {{ font-size: .82rem; font-weight: 600; color: var(--text); }}
  .fx-equals      {{ font-size: .68rem; color: var(--muted); }}
  .fx-rate        {{ font-size: 1.1rem; font-weight: 800; color: #06b6d4; }}

  /* ── Tasks ── */
  .card.tasks {{ border-color: #8b5cf6; }}
  .task-item {{
    display: flex; gap: 12px; padding: 10px 0;
    border-bottom: 1px solid var(--border); align-items: flex-start;
  }}
  .task-item:last-child {{ border-bottom: none; padding-bottom: 0; }}
  .task-item.task-is-overdue {{ background: #fff1f2; margin: 0 -8px; padding: 10px 8px; border-radius: 8px; border-bottom: none; }}
  .task-check {{ font-size: 1rem; color: #8b5cf6; padding-top: 1px; flex-shrink: 0; }}
  .task-title {{ font-size: .88rem; font-weight: 600; line-height: 1.4; }}
  .task-overdue {{
    font-size: .65rem; font-weight: 700; background: #fee2e2; color: #991b1b;
    border: 1px solid #fca5a5; padding: 1px 7px; border-radius: 20px;
    vertical-align: middle; margin-left: 6px;
  }}
  .task-due   {{ font-size: .72rem; color: var(--muted); display: block; margin-top: 3px; }}
  .task-notes {{ font-size: .75rem; color: var(--muted); margin-top: 3px; font-style: italic; line-height: 1.4; }}

  /* ── Word of the Day ── */
  .word-header {{
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 10px;
    margin-bottom: 12px;
  }}
  .word-text {{ font-size: 1.7rem; font-weight: 800; color: var(--amber); letter-spacing: -.01em; }}
  .word-pos  {{
    font-size: .75rem;
    font-style: italic;
    color: var(--pink);
    background: #fdf2f8;
    padding: 2px 9px;
    border-radius: 20px;
  }}
  .word-pron {{ font-size: .83rem; color: var(--muted); }}
  .word-etymology {{ font-size: .78rem; color: var(--muted); line-height: 1.55; margin-bottom: 8px; font-style: italic; border-left: 3px solid var(--amber); padding-left: 10px; }}
  .word-def  {{ font-size: .88rem; line-height: 1.65; margin-bottom: 8px; }}
  .word-ex   {{ font-size: .83rem; color: var(--muted); font-style: italic; line-height: 1.55; }}

  /* ── Fun Fact ── */
  .card.fact p {{ font-size: .9rem; line-height: 1.75; }}

  .empty {{ color: var(--muted); font-style: italic; font-size: .88rem; }}
</style>
</head>
<body>
<div class="wrap">

  <header>
    <h1>Good morning ☀️</h1>
    <span class="date">{today}</span>
  </header>

  <div class="grid">

    <!-- Weather -->
    <div class="card weather">
      <div class="section-label">🌤 London Weather</div>
      {weather_html}
    </div>

    <!-- Currency -->
    <div class="card currency">
      <div class="section-label">💱 Exchange Rates</div>
      {currency_html}
    </div>

    <!-- Word of the Day -->
    <div class="card word full">
      <div class="section-label">📖 Word of the Day</div>
      {word_html}
    </div>

    <!-- Top Headlines -->
    <div class="card news full">
      <div class="section-label">📰 Top Headlines</div>
      {news_html}
    </div>

    <!-- Calendar -->
    <div class="card calendar full">
      <div class="section-label">📅 3-Day Schedule</div>
      {calendar_html}
    </div>

    <!-- Tasks -->
    <div class="card tasks full">
      <div class="section-label">✅ My Tasks</div>
      {tasks_html}
    </div>

    <!-- Fun Fact -->
    <div class="card fact full">
      <div class="section-label">💡 Fun Fact</div>
      <p>{fun_fact}</p>
    </div>

  </div>
</div>
</body>
</html>
"""


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print("🌅  Morning Briefing Agent\n")

    print("→ Authenticating with Google…")
    creds = get_google_creds()

    print("→ Fetching weather…")
    weather = fetch_weather()

    print("→ Fetching exchange rates…")
    rates = fetch_currency_rates()

    print("→ Fetching news headlines…")
    raw_news = fetch_news()

    print("→ Fetching calendar events (3 days)…")
    events = fetch_calendar_events(creds)

    print("→ Fetching tasks…")
    tasks = fetch_tasks(creds)

    print("→ Generating content with Gemini…")
    gemini = fetch_gemini_content()

    print("→ Filtering and summarising news with Gemini…")
    news = summarize_news_with_gemini(raw_news)

    print("→ Building briefing.html…")
    html = generate_html(weather, news, events, gemini, rates, tasks)
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    print(f"  Saved → {OUTPUT_FILE.resolve()}")

    print("→ Uploading to Netlify…")
    netlify_url = upload_to_netlify(html)

    print("→ Sending notification email…")
    send_email(creds, netlify_url)

    print("→ Sending desktop notification…")
    open_url = netlify_url if netlify_url else f"file://{OUTPUT_FILE.resolve()}"
    os.system(
        f'/opt/homebrew/bin/terminal-notifier'
        f' -title "Good morning! Your briefing is ready ☀️"'
        f' -message "Click to open your morning briefing"'
        f' -execute \'open -a "Google Chrome" "{open_url}"\''
        f' -sound Glass'
    )

    print("\n✅  Done! Check your email for the link.")


if __name__ == "__main__":
    main()
