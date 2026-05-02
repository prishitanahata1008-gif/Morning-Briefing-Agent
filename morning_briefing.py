#!/usr/bin/env python3
"""
Jarvis
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
import sys
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
VERCEL_TOKEN    = os.environ["VERCEL_TOKEN"]
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
PREVIEW_FILE      = BASE_DIR / "preview.html"
VERCEL_PROJECT_NAME = "morning-briefing-prishita"
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
    """Fetch headlines from multiple sources — Gemini will filter and summarise them."""
    articles = []
    try:
        # Top headlines by country for US and UK coverage
        for country in ("us", "gb"):
            r = requests.get(
                "https://newsapi.org/v2/top-headlines",
                params={
                    "country":  country,
                    "pageSize": 20,
                    "apiKey":   NEWS_API_KEY,
                },
                timeout=10,
            )
            r.raise_for_status()
            articles += r.json().get("articles", [])

        # Business headlines
        r = requests.get(
            "https://newsapi.org/v2/top-headlines",
            params={
                "language": "en",
                "category": "business",
                "pageSize": 15,
                "apiKey":   NEWS_API_KEY,
            },
            timeout=10,
        )
        r.raise_for_status()
        articles += r.json().get("articles", [])

        # Deduplicate and format
        seen = set()
        unique = []
        for a in articles:
            title = a.get("title", "")
            if title and title not in seen:
                seen.add(title)
                unique.append({
                    "title":       title,
                    "source":      a.get("source", {}).get("name", ""),
                    "url":         a.get("url", "#"),
                    "description": (a.get("description") or ""),
                })
        return unique
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

def fetch_gold_price() -> dict | None:
    """Fetch gold spot price (XAU/USD) from Yahoo Finance. No API key needed."""
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/GC%3DF",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        usd_per_oz = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
        return {"usd_per_oz": round(usd_per_oz, 2)}
    except Exception as e:
        print(f"  [Gold] Error: {e}")
        return None


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
    """Single entry point for all Gemini calls. Retries on 503 before giving up."""
    import time
    client = genai.Client(api_key=GEMINI_API_KEY)
    for attempt in range(3):
        try:
            response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
            return response.text.strip()
        except Exception as e:
            if "503" in str(e) and attempt < 2:
                print(f"  [Gemini] Server busy, retrying in 10s… (attempt {attempt + 1}/3)")
                time.sleep(10)
            else:
                raise


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
            "   - Definition: rich and precise, capturing every nuance — not a dictionary stub\n"
            "   - Example: one vivid, literary sentence where the word fits so naturally its meaning is unmistakable\n\n"
            "2. EDUCATIONAL FACT — For a morning briefing. Rules:\n"
            "   - 2-3 sentences maximum. Use only what's needed to explain it clearly.\n"
            "   - Conversational and crisp — write like a smart friend explaining something, not like Wikipedia\n"
            "   - No passive voice, no filler phrases like 'it is worth noting', no exclamation marks\n"
            "   - Must be counterintuitive, surprising, or reframe something familiar\n"
            "   - Must have a clear takeaway — the reader finishes thinking 'huh, that changes how I see X'\n"
            "   - Topics: science, psychology, economics, nature, history, human behaviour\n"
            "   - If historical, it must reveal something about human nature, systems, or decision-making — not just a strange event\n"
            "   - Avoid pure anecdotes with no broader insight\n\n"
            "Respond ONLY with valid JSON, no markdown fences:\n"
            '{"word":"...","pronunciation":"...","part_of_speech":"...","definition":"...","example":"...","fun_fact":"..."}'
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
            "You are a news editor picking today's front page stories. Here are the available headlines:\n\n"
            f"{headlines_text}\n\n"
            "Pick the 5 most important stories that belong on the front page of a major newspaper. "
            "Focus on: active wars and conflicts, major US politics, major UK politics, major India politics, "
            "and significant global business or economic news. "
            "Ignore: local crime, random shootings, sports, celebrity, entertainment, lifestyle, minor local politics. "
            "If a story is confusing without context, skip it.\n\n"
            "For each story write 2-3 simple plain sentences: what happened and why it matters. "
            "Write like you're telling a friend — clear, direct, no jargon, no filler phrases.\n\n"
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


# ── VERCEL ────────────────────────────────────────────────────────────────────

def upload_to_vercel(html_content: str) -> str:
    """Deploy briefing as index.html to Vercel. Same project name = same URL every day."""
    headers = {
        "Authorization": f"Bearer {VERCEL_TOKEN}",
        "Content-Type":  "application/json",
    }

    r = requests.post(
        "https://api.vercel.com/v13/deployments",
        headers=headers,
        json={
            "name": VERCEL_PROJECT_NAME,
            "target": "production",
            "files": [
                {"file": "index.html", "data": html_content},
                {"file": "vercel.json", "data": '{"headers":[{"source":"/(.*)", "headers":[{"key":"Cache-Control","value":"no-store, no-cache, must-revalidate"}]}]}'},
            ],
            "projectSettings": {"framework": None},
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    url = f"https://{VERCEL_PROJECT_NAME}.vercel.app"
    print(f"  Deployed → {url}")
    return url


# ── EMAIL ─────────────────────────────────────────────────────────────────────

def send_email(creds: Credentials, briefing_url: str) -> None:
    try:
        service = build("gmail", "v1", credentials=creds)

        msg                    = MIMEMultipart("alternative")
        msg["Subject"]         = "☀️ Your Morning Briefing is Ready"
        msg["From"]            = f"Jarvis <{YOUR_EMAIL}>"
        msg["To"]              = YOUR_EMAIL
        msg["X-Morning-Brief"] = "true"

        plain = f"Good morning!\n\nYour briefing is ready:\n\n{briefing_url}\n\nHave a great day!"
        html  = f"""<p>Good morning!</p>
<p>Your morning briefing is ready:</p>
<p><a href="{briefing_url}" style="font-size:16px;font-weight:bold;">☀️ Open Morning Briefing</a></p>
<p>Have a great day!</p>"""

        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(html,  "html"))

        raw    = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        result = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        print(f"  Email sent to {YOUR_EMAIL} — Message ID: {result.get('id')}")
    except Exception as e:
        print(f"  [Email] Error: {e}")
        raise


# ── HTML GENERATION ───────────────────────────────────────────────────────────

def _build_weather_html(weather: dict | None) -> str:
    if not weather:
        return "<p class='empty'>Weather unavailable.</p>"
    cond = weather['description'].capitalize()
    return f"""<div class="temp-big">{weather['temp_c']}°</div>
        <div class="temp-desc">{cond}</div>
        <div class="weather-sub">
            <div class="wsub-item"><span class="wsub-label">Feels like</span><span class="wsub-val">{weather['feels_like_c']}°C</span></div>
            <div class="wsub-item"><span class="wsub-label">High / Low</span><span class="wsub-val">{weather['max_c']}° / {weather['min_c']}°</span></div>
            <div class="wsub-item"><span class="wsub-label">Humidity</span><span class="wsub-val">{weather['humidity']}%</span></div>
            <div class="wsub-item"><span class="wsub-label">Wind</span><span class="wsub-val">{weather['wind_kmph']} km/h</span></div>
        </div>"""


def _build_news_html(news: list[dict]) -> str:
    if not news:
        return "<p class='empty'>No headlines available.</p>"
    items = ""
    for i, a in enumerate(news, 1):
        url = a.get("url", "#")
        items += f"""
            <div class="news-item">
                <span class="news-num">{i:02d}</span>
                <a href="{url}" target="_blank" class="news-title">{a['title']}</a>
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
                end_part = f" – {e['end']}" if e["end"] and e["end"] != e["start"] else ""
                deadline = "<span class='deadline-badge'>Deadline</span>" if e["is_deadline"] else ""
                loc      = f"<span class='event-loc'>{e['location']}</span>" if e["location"] else ""
                html += f"""
                <div class="event-item">
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
    entries = [("GBP", "British Pound"), ("EUR", "Euro"), ("USD", "US Dollar")]
    rows = ""
    for code, label in entries:
        rate = rates.get(code)
        if rate is not None:
            rows += f"""
            <div class="fx-row">
                <span class="fx-code">{code}</span>
                <span class="fx-label-sm">{label}</span>
                <span class="fx-rate">&#8377;{rate:,.2f}</span>
            </div>"""
    return rows


def _build_gold_html(gold: dict | None, rates: dict | None) -> str:
    if not gold or not rates or not rates.get("USD"):
        return "<p class='empty'>Gold data unavailable.</p>"
    usd_per_oz    = gold["usd_per_oz"]
    usd_per_gram  = usd_per_oz / 31.1035
    inr_per_gram  = usd_per_gram * rates["USD"]
    inr_per_10g   = inr_per_gram * 10
    gbp_per_gram  = usd_per_gram * (rates["USD"] / rates["GBP"]) if rates.get("GBP") else None
    gbp_str       = f"&#163;{gbp_per_gram:,.2f}" if gbp_per_gram else "—"
    return f"""
        <div class="fx-row">
            <span class="fx-code">INR</span>
            <span class="fx-label-sm">per 10g (24K)</span>
            <span class="fx-rate">&#8377;{inr_per_10g:,.0f}</span>
        </div>
        <div class="fx-row">
            <span class="fx-code">USD</span>
            <span class="fx-label-sm">per troy oz</span>
            <span class="fx-rate">${usd_per_oz:,.0f}</span>
        </div>
        <div class="fx-row">
            <span class="fx-code">GBP</span>
            <span class="fx-label-sm">per gram</span>
            <span class="fx-rate">{gbp_str}</span>
        </div>"""


def _build_tasks_html(tasks: list[dict]) -> str:
    if not tasks:
        return "<p class='empty'>No pending tasks.</p>"
    items = ""
    for t in tasks:
        due_html   = f"<span class='task-due'>Due {t['due']}</span>" if t["due"] else ""
        overdue    = "<span class='task-overdue'>Overdue</span>" if t["overdue"] else ""
        notes_html = f"<p class='task-notes'>{t['notes']}</p>" if t["notes"] else ""
        items += f"""
        <div class="task-item">
            <div class="task-dot{'  task-dot-overdue' if t['overdue'] else ''}"></div>
            <div class="task-body">
                <div class="task-title">{t['title']} {overdue}</div>
                {due_html}{notes_html}
            </div>
        </div>"""
    return items


def _build_word_html(g: dict) -> str:
    pos     = f'<span class="word-pos">{g.get("part_of_speech", "")}</span>' if g.get("part_of_speech") else ""
    example = f'<p class="word-ex">"{g.get("example", "")}"</p>' if g.get("example") else ""
    return f"""
        <div class="word-head">
            <span class="word-text">{g.get('word', '')}</span>{pos}
        </div>
        <p class="word-def">{g.get('definition', '')}</p>
        {example}"""


def generate_html(weather, news, events, gemini, rates, tasks, gold=None, theme: str = "soft-red") -> str:
    today         = datetime.date.today().strftime("%A, %B %-d, %Y")
    weather_html  = _build_weather_html(weather)
    news_html     = _build_news_html(news)
    calendar_html = _build_calendar_html(events)
    word_html     = _build_word_html(gemini)
    fun_fact      = gemini.get("fun_fact", "")
    currency_html = _build_currency_html(rates)
    gold_html     = _build_gold_html(gold, rates)
    tasks_html    = _build_tasks_html(tasks)

    palette = {
        "bg_cream": "#f8efec",
        "bg_tint": "#f2e4df",
        "tile_bg": "#fffdfc",
        "ink_main": "#26171b",
        "ink_soft": "#5f5054",
        "ink_muted": "#907f84",
        "line_soft": "#e6d4cf",
        "accent": "#8c1d1e",
        "accent_deep": "#5f0f12",
    }

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Jarvis</title>
<style>
    :root {{
        --bg-cream: {palette['bg_cream']};
        --bg-tint: {palette['bg_tint']};
        --tile-bg: {palette['tile_bg']};
        --ink-main: {palette['ink_main']};
        --ink-soft: {palette['ink_soft']};
        --ink-muted: {palette['ink_muted']};
        --line-soft: {palette['line_soft']};
        --accent: {palette['accent']};
        --accent-deep: {palette['accent_deep']};
    }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                background: linear-gradient(180deg, var(--bg-cream), var(--bg-tint));
        color: var(--ink-main);
    min-height: 100vh;
    padding: 32px 20px 64px;
  }}
  .wrap {{ max-width: 960px; margin: 0 auto; }}

  /* ── Header ── */
  .page-header {{
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    padding-bottom: 20px;
    margin-bottom: 16px;
        border-bottom: 1px solid var(--line-soft);
  }}
  .jarvis-tag {{
    font-size: 9px;
    font-weight: 600;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: var(--accent);
    display: flex;
    align-items: center;
    gap: 7px;
    margin-bottom: 10px;
  }}
  .jarvis-dot {{
    width: 6px; height: 6px;
    border-radius: 50%;
        background: var(--accent);
    flex-shrink: 0;
    animation: blink 2s infinite;
  }}
  @keyframes blink {{
    0%, 100% {{ opacity: 1; transform: scale(1); }}
    50%       {{ opacity: 0.35; transform: scale(0.75); }}
  }}
  .header-greeting {{
    font-family: Georgia, serif;
    font-size: 30px;
    font-weight: normal;
        color: var(--ink-main);
    line-height: 1.15;
    margin-bottom: 5px;
  }}
  .header-sub {{
    font-size: 12px;
        color: var(--accent);
        font-style: italic;
    line-height: 1.5;
  }}
  .header-date {{
    font-size: 11px;
        color: var(--ink-muted);
    text-align: right;
    line-height: 1.8;
  }}

  /* ── Bento grid ── */
  .bento {{
    display: grid;
    grid-template-columns: repeat(12, 1fr);
    gap: 10px;
  }}
  @media (max-width: 640px) {{
    .bento {{ grid-template-columns: 1fr; }}
    [class*="span-"] {{ grid-column: 1 / -1 !important; }}
  }}
  .span-4  {{ grid-column: span 4; }}
  .span-6  {{ grid-column: span 6; }}
  .span-12 {{ grid-column: span 12; }}

  /* ── Tile base ── */
  .tile {{
    border-radius: 10px;
    padding: 18px 20px;
                border: 0.5px solid #dcc6c0;
                border-left: 3px solid var(--accent);
                background: linear-gradient(180deg, #fffefe 0%, var(--tile-bg) 100%);
                box-shadow: 0 6px 18px rgba(95, 15, 18, 0.06);
  }}

    /* Legacy markup compatibility: older briefings use .hero/.grid/.card/.section-label */
    .hero {{
        padding-bottom: 20px;
        border-bottom: 1px solid var(--line-soft);
        margin-bottom: 16px;
    }}
    .hero-tag {{
        font-size: 9px;
        font-weight: 600;
        letter-spacing: 0.18em;
        text-transform: uppercase;
        color: var(--accent);
        margin-bottom: 10px;
    }}
    .hero h1 {{
        font-family: Georgia, serif;
        font-size: 30px;
        line-height: 1.15;
        color: var(--ink-main);
        margin-bottom: 5px;
    }}
    .hero-sub {{
        font-size: 12px;
        color: var(--accent);
        font-style: italic;
        line-height: 1.5;
    }}
    .hero-date {{
        font-size: 11px;
        color: var(--ink-muted);
        line-height: 1.8;
    }}
    .grid {{
        display: grid;
        grid-template-columns: repeat(12, 1fr);
        gap: 10px;
    }}
    .card {{
        border-radius: 10px;
        padding: 18px 20px;
        border: 0.5px solid #dcc6c0;
        border-left: 3px solid var(--accent);
        background: linear-gradient(180deg, #fffefe 0%, var(--tile-bg) 100%);
        box-shadow: 0 6px 18px rgba(95, 15, 18, 0.06);
    }}
    .weather, .currency {{ grid-column: span 6; }}
    .full {{ grid-column: 1 / -1; }}
    .section-label {{
        font-size: 9px;
        font-weight: 500;
        letter-spacing: 0.14em;
        text-transform: uppercase;
        color: var(--accent);
        margin-bottom: 12px;
    }}
    .weather-main {{
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 8px;
    }}
    .weather-icon {{ font-size: 22px; }}
    .temp {{
        font-family: Georgia, serif;
        font-size: 40px;
        color: var(--accent-deep);
        line-height: 1;
    }}
    .desc {{ font-size: 12px; color: var(--ink-muted); }}
    .weather-meta {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 6px;
    }}
    .meta-item span {{
        font-size: 9px;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: var(--ink-muted);
        display: block;
    }}
    .meta-item strong {{ font-size: 13px; color: var(--ink-main); }}
    .fx-grid {{ display: grid; gap: 8px; }}
    .fx-item {{
        display: grid;
        grid-template-columns: auto 1fr auto;
        gap: 8px;
        align-items: center;
        border-bottom: 0.5px solid var(--line-soft);
        padding-bottom: 8px;
    }}
    .fx-item:last-child {{ border-bottom: none; padding-bottom: 0; }}
    .fx-flag {{ font-size: 14px; }}
    .fx-label-group {{ display: flex; gap: 5px; flex-wrap: wrap; color: var(--ink-main); }}
    .fx-equals {{ color: var(--ink-muted); }}
    .news-source {{ font-size: 10px; color: var(--ink-muted); margin-top: 2px; }}
    .news-desc {{ font-size: 12px; color: var(--ink-soft); line-height: 1.6; margin-top: 5px; }}
    .read-more {{ color: var(--accent); text-decoration: none; font-size: 11px; display: inline-block; margin-top: 6px; }}
    .task-check {{ color: var(--ink-muted); margin-top: 2px; display: inline-block; }}
    .word-header {{ display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; margin-bottom: 6px; }}
    .word-pron {{ font-size: 11px; color: var(--ink-muted); }}
    .word-etymology {{ font-size: 11px; color: var(--ink-muted); font-style: italic; line-height: 1.5; margin-bottom: 6px; }}
    @media (max-width: 640px) {{
        .weather, .currency, .full {{ grid-column: 1 / -1; }}
    }}

  /* ── Section label ── */
  .label {{
    font-size: 9px;
    font-weight: 500;
    letter-spacing: 0.14em;
    text-transform: uppercase;
        color: var(--accent);
    margin-bottom: 12px;
  }}

  /* ── Weather tile ── */
  .temp-big {{
    font-family: Georgia, serif;
    font-size: 56px;
        color: var(--accent-deep);
    line-height: 1;
    margin-bottom: 4px;
  }}
  .temp-desc {{
    font-size: 11px;
        color: var(--ink-muted);
    text-transform: capitalize;
    margin-bottom: 14px;
  }}
  .weather-sub {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px;
  }}
  .wsub-item {{ display: flex; flex-direction: column; gap: 2px; }}
  .wsub-label {{
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
        color: var(--ink-muted);
  }}
    .wsub-val {{ font-size: 13px; color: var(--ink-main); }}

  /* ── FX + Gold tiles ── */
  .fx-row {{
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    padding: 8px 0;
        border-bottom: 0.5px solid var(--line-soft);
  }}
  .fx-row:last-child {{ border-bottom: none; padding-bottom: 0; }}
    .fx-code     {{ font-size: 11px; color: var(--ink-main); font-weight: 600; min-width: 36px; }}
    .fx-label-sm {{ font-size: 10px; color: var(--ink-muted); flex: 1; padding-left: 8px; }}
    .fx-rate     {{ font-family: Georgia, serif; font-size: 16px; color: var(--accent); }}

  /* ── Headlines tile ── */
  .news-item {{
    display: flex;
    align-items: baseline;
    gap: 10px;
    padding: 8px 0;
        border-bottom: 0.5px solid var(--line-soft);
  }}
  .news-item:last-child {{ border-bottom: none; padding-bottom: 0; }}
  .news-num {{
    font-family: Georgia, serif;
    font-size: 11px;
    color: #d9b4b5;
    min-width: 20px;
    flex-shrink: 0;
  }}
  .news-title {{
    font-size: 12px;
        color: var(--ink-soft);
    line-height: 1.5;
    text-decoration: none;
  }}
    .news-title:hover {{ color: var(--accent); }}

  /* ── Calendar tile ── */
  .cal-day-label {{
    font-size: 9px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--accent);
    margin: 14px 0 6px;
    padding-bottom: 5px;
    border-bottom: 0.5px solid var(--line-soft);
    display: flex;
    gap: 8px;
  }}
  .cal-day-label:first-child {{ margin-top: 0; }}
    .cal-day-date {{ font-weight: 400; color: var(--ink-muted); text-transform: none; letter-spacing: 0; }}
    .cal-empty {{ font-size: 11px; color: var(--ink-muted); font-style: italic; padding: 4px 0 8px; }}
  .event-item {{
    display: flex;
    gap: 14px;
    padding: 7px 0;
    border-bottom: 0.5px solid var(--line-soft);
  }}
  .event-item:last-child {{ border-bottom: none; padding-bottom: 0; }}
  .event-time {{
    font-size: 11px;
    color: var(--ink-muted);
    font-family: 'SF Mono', 'Menlo', monospace;
    min-width: 90px;
    flex-shrink: 0;
    padding-top: 1px;
  }}
    .event-title {{ font-size: 13px; color: var(--ink-main); line-height: 1.4; }}
    .event-loc   {{ font-size: 11px; color: var(--ink-muted); display: block; margin-top: 2px; }}
  .deadline-badge {{
    font-size: 9px;
    font-weight: 500;
    background: #f8e9e6;
    color: var(--accent);
    border: 0.5px solid #e1c6bf;
    padding: 1px 6px;
    border-radius: 20px;
    vertical-align: middle;
    margin-left: 6px;
    letter-spacing: 0.05em;
  }}

  /* ── Tasks tile ── */
  .task-item {{
    display: flex;
    gap: 12px;
    padding: 7px 0;
    border-bottom: 0.5px solid var(--line-soft);
    align-items: flex-start;
  }}
  .task-item:last-child {{ border-bottom: none; padding-bottom: 0; }}
  .task-dot {{
    width: 6px; height: 6px;
    border-radius: 50%;
    background: #d8b8b1;
    flex-shrink: 0;
    margin-top: 5px;
  }}
    .task-dot-overdue {{ background: var(--accent); box-shadow: 0 0 0 3px rgba(140, 29, 30, 0.12); }}
    .task-title {{ font-size: 13px; color: var(--ink-main); line-height: 1.4; }}
  .task-overdue {{
    font-size: 9px;
    font-weight: 500;
    background: #f8e9e6;
    color: var(--accent);
    border: 0.5px solid #e1c6bf;
    padding: 1px 6px;
    border-radius: 20px;
    vertical-align: middle;
    margin-left: 6px;
    letter-spacing: 0.05em;
  }}
    .task-due   {{ font-size: 11px; color: var(--ink-muted); display: block; margin-top: 2px; }}
    .task-notes {{ font-size: 11px; color: var(--ink-muted); margin-top: 2px; font-style: italic; line-height: 1.4; }}

  /* ── Word of the Day tile ── */
  .word-head {{ display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; flex-wrap: wrap; }}
    .word-text {{ font-family: Georgia, serif; font-size: 24px; color: var(--accent-deep); font-weight: normal; }}
    .word-pos  {{ font-size: 11px; font-style: italic; color: var(--ink-muted); }}
    .word-def  {{ font-size: 12px; color: var(--ink-soft); line-height: 1.7; margin-bottom: 8px; }}
    .word-ex   {{ font-size: 11px; color: var(--ink-muted); font-style: italic; line-height: 1.6; }}

  /* ── Fact tile ── */
    .fact-text {{ font-size: 13px; color: var(--ink-soft); line-height: 1.8; }}

    .empty {{ color: var(--ink-muted); font-style: italic; font-size: 12px; }}
</style>
</head>
<body>
<div class="wrap">

  <!-- Header -->
  <div class="page-header">
    <div class="header-left">
      <div class="jarvis-tag"><span class="jarvis-dot"></span>Jarvis &mdash; Online</div>
      <div class="header-greeting">Good morning, Prish.</div>
      <div class="header-sub">Here's your briefing. Let's get you ready for the day.</div>
    </div>
    <div class="header-date">{today}</div>
  </div>

  <div class="bento">

    <!-- Weather -->
    <div class="tile span-4">
      <div class="label">London Weather</div>
      {weather_html}
    </div>

    <!-- Exchange Rates -->
    <div class="tile span-4">
      <div class="label">Exchange Rates &rarr; INR</div>
      {currency_html}
    </div>

    <!-- Gold -->
    <div class="tile span-4">
      <div class="label">Gold</div>
      {gold_html}
    </div>

    <!-- Calendar -->
    <div class="tile span-6">
      <div class="label">3-Day Schedule</div>
      {calendar_html}
    </div>

    <!-- Tasks -->
    <div class="tile span-6">
      <div class="label">My Tasks</div>
      {tasks_html}
    </div>

    <!-- Headlines -->
    <div class="tile span-12">
      <div class="label">Headlines</div>
      {news_html}
    </div>

    <!-- Word of the Day -->
    <div class="tile span-6">
      <div class="label">Word of the Day</div>
      {word_html}
    </div>

    <!-- Fact -->
    <div class="tile span-6">
      <div class="label">Today's Fact</div>
      <p class="fact-text">{fun_fact}</p>
    </div>

  </div>
</div>
</body>
</html>
"""


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    preview_only = "--preview" in sys.argv
    preview_live = "--preview-live" in sys.argv
    mode_suffix = " (Preview Mode)" if preview_only else ""
    print(f"🌅  Jarvis{mode_suffix}\n")

    if preview_only and not preview_live:
        out_file = PREVIEW_FILE
        print("→ Building design-only preview with sample content (soft-red)…")
        sample_weather = {
            "temp_c": "14",
            "feels_like_c": "12",
            "description": "Overcast clouds",
            "humidity": "78",
            "wind_kmph": "19",
            "max_c": "16",
            "min_c": "10",
            "icon": "☁️",
        }
        sample_rates = {"GBP": 107.42, "EUR": 90.18, "USD": 83.57}
        sample_gold = {"usd_per_oz": 3289.0}
        sample_news = [
            {"title": "US and EU prepare fresh trade talks amid tariff pressure", "source": "Reuters", "url": "#", "summary": ""},
            {"title": "India announces logistics reform push to cut export costs", "source": "Bloomberg", "url": "#", "summary": ""},
            {"title": "Bank of England signals caution as inflation cools unevenly", "source": "Financial Times", "url": "#", "summary": ""},
            {"title": "Oil slips as inventories rise, markets watch shipping lanes", "source": "WSJ", "url": "#", "summary": ""},
            {"title": "Chipmakers rally on new data-center demand forecast", "source": "CNBC", "url": "#", "summary": ""},
        ]
        today = datetime.date.today()
        sample_events = [
            {
                "label": "Today",
                "date": today.strftime("%b %-d"),
                "events": [
                    {"summary": "Team standup", "start": "9:00 AM", "end": "10:00 AM", "location": "Google Meet", "is_deadline": False},
                    {"summary": "Product review", "start": "2:00 PM", "end": "3:30 PM", "location": "Conf Room A", "is_deadline": True},
                ],
            },
            {
                "label": "Tomorrow",
                "date": (today + datetime.timedelta(days=1)).strftime("%b %-d"),
                "events": [
                    {"summary": "Client sync", "start": "11:00 AM", "end": "11:45 AM", "location": "Zoom", "is_deadline": False},
                ],
            },
            {
                "label": (today + datetime.timedelta(days=2)).strftime("%A, %b %-d"),
                "date": (today + datetime.timedelta(days=2)).strftime("%b %-d"),
                "events": [],
            },
        ]
        sample_tasks = [
            {"title": "Review Q2 analytics report", "due": "Sat, May 3", "overdue": False, "list": "Work", "notes": ""},
            {"title": "Submit expense claims", "due": "Mon, Apr 28", "overdue": True, "list": "Admin", "notes": ""},
            {"title": "Read chapter 4 of Thinking Fast and Slow", "due": "", "overdue": False, "list": "Personal", "notes": ""},
        ]
        sample_gemini = {
            "word": "Liminal",
            "part_of_speech": "adjective",
            "definition": "Describing a threshold moment where one state has ended but the next one has not fully formed yet.",
            "example": "The station platform at dawn felt liminal, suspended between yesterday's fatigue and today's momentum.",
            "fun_fact": "People estimate waiting time as shorter when they can see progress, even if the actual wait is identical. That is why tiny indicators like loading bars reduce frustration more than raw speed alone.",
        }

        html = generate_html(
            sample_weather,
            sample_news,
            sample_events,
            sample_gemini,
            sample_rates,
            sample_tasks,
            sample_gold,
            theme="soft-red",
        )
        out_file.write_text(html, encoding="utf-8")
        print(f"  Saved → {out_file.resolve()}")
        os.system(f'/usr/bin/open -a "Google Chrome" "{out_file.resolve()}"')
        print("\n✅  Design preview ready. No live data was fetched and no deploy/email was performed.")
        return

    # Give the desktop time to fully load when run at login via launchd
    if not os.isatty(0):
        import time
        time.sleep(30)

    print("→ Authenticating with Google…")
    creds = get_google_creds()

    print("→ Fetching weather…")
    weather = fetch_weather()

    print("→ Fetching exchange rates…")
    rates = fetch_currency_rates()

    print("→ Fetching gold price…")
    gold = fetch_gold_price()

    print("→ Fetching news headlines…")
    raw_news = fetch_news()

    print("→ Fetching calendar events (3 days)…")
    events = fetch_calendar_events(creds)

    print("→ Fetching tasks…")
    tasks = fetch_tasks(creds)

    print("→ Generating content with Gemini…")
    gemini = fetch_gemini_content()

    import time
    time.sleep(15)

    print("→ Filtering and summarising news with Gemini…")
    news = summarize_news_with_gemini(raw_news)

    print("→ Building briefing HTML…")
    html = generate_html(weather, news, events, gemini, rates, tasks, gold)
    out_file = PREVIEW_FILE if preview_only else OUTPUT_FILE
    out_file.write_text(html, encoding="utf-8")
    print(f"  Saved → {out_file.resolve()}")

    if preview_only:
        print("→ Opening local preview in Chrome…")
        preview_url = f"file://{out_file.resolve()}"
        os.system(f'launchctl asuser 501 /usr/bin/open -a "Google Chrome" "{preview_url}"')
        print("\n✅  Preview ready. No deploy or email actions were performed.")
        return

    print("→ Uploading to Vercel…")
    vercel_url = upload_to_vercel(html)

    print("→ Sending notification email…")
    send_email(creds, vercel_url)

    print("→ Sending desktop notification…")
    open_url = vercel_url if vercel_url else f"file://{OUTPUT_FILE.resolve()}"
    os.system(
        f'launchctl asuser 501 osascript -e \''
        f'display notification "Your briefing is ready" '
        f'with title "☀️ Good morning!" subtitle "Jarvis" sound name "Glass"\''
    )
    os.system(f'launchctl asuser 501 /usr/bin/open -a "Google Chrome" "{open_url}"')

    print("\n✅  Done! Check your email for the link.")


if __name__ == "__main__":
    main()
