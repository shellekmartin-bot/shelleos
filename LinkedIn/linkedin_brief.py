#!/usr/bin/env python3
"""
LinkedIn Brief Generator — Shelle Martin
Fetches M&A news via Google News RSS, generates 3 LinkedIn post options
per company using Claude, and emails the brief.

Schedule: Monday & Thursday at 4 AM Pacific
.env requires: ANTHROPIC_API_KEY, GMAIL_FROM, GMAIL_APP_PASSWORD
"""

import os
import re
import sys
import smtplib
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from dotenv import load_dotenv
import httpx
import anthropic

# ─── Credentials ──────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(SCRIPT_DIR, '..', '.env'), override=True)

ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY")
GMAIL_FROM         = os.getenv("GMAIL_FROM")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
GMAIL_TO           = os.getenv("GMAIL_TO", "shelle.martin@datasite.com")

missing = [k for k, v in {
    "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
    "GMAIL_FROM": GMAIL_FROM,
    "GMAIL_APP_PASSWORD": GMAIL_APP_PASSWORD,
}.items() if not v]
if missing:
    print(f"ERROR: Missing env vars: {', '.join(missing)}")
    sys.exit(1)

http_client = httpx.Client()
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, http_client=http_client)
TODAY = date.today().isoformat()
DAY = datetime.today().strftime("%A")

# ─── Companies to monitor ─────────────────────────────────────────────────────
COMPANIES = [
    "Datasite",
    "Salesforce",
    "ServiceNow",
    "Palo Alto Networks",
    "Snowflake",
]

# ─── Persona ──────────────────────────────────────────────────────────────────
PERSONA = """You are writing LinkedIn posts for Shelle Martin.

Who Shelle is:
- Enterprise Sales Manager at Datasite (M&A lifecycle platform)
- Bay Area since 2005. 2,300+ deals closed. 210+ strategic accounts.
- 56 years old. Gen X. Artist (watercolors, botanicals, West Coast landscapes).
- Peace Corps background. Two pitbulls. Three kids.
- Super AI-forward, which surprises people given her background.
- Voice: confident, direct, slightly irreverent. No fluff. Earned opinions.

Writing rules:
- Never use: "game-changer", "leverage", "robust", "synergy", "unlock", "transformative", "reimagine", "groundbreaking", "cutting-edge", "innovative"
- Use em dashes (...)  for pauses, not parentheses
- Short punchy sentences. No bullet lists.
- Watercolor / art references are welcome but not required
- Occasionally reference "2,300+ deals" or "after 20 years in M&A"
- Max 3 hashtags per post, at the end only
- End with something that invites reflection, not a question"""

# ─── Day-specific intro ───────────────────────────────────────────────────────
MONDAY_INTRO = """Happy Monday! Here are your LinkedIn posts for the week.
Pick one per company or mix and match. Ready to post as-is or tweak to taste.
"""

THURSDAY_INTRO = """Mid-week pulse check. Fresh posts below based on what's moving in the market.
Ready to copy/paste or edit as you like.
"""

# ─── Google News RSS ──────────────────────────────────────────────────────────
def fetch_news(company, lookback_days=4):
    query = f'"{company}" M&A OR acquisition OR deal OR earnings OR AI'
    encoded = quote_plus(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    try:
        resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        print(f"  WARNING: News fetch failed for {company}: {e}")
        return []

    cutoff = datetime.now() - timedelta(days=lookback_days)
    articles = []
    for item in root.findall(".//item")[:8]:
        title = item.findtext("title") or ""
        desc = item.findtext("description") or ""
        pub_date_str = item.findtext("pubDate") or ""
        link = item.findtext("link") or ""
        try:
            pub_date = parsedate_to_datetime(pub_date_str).replace(tzinfo=None)
            if pub_date < cutoff:
                continue
        except Exception:
            pass
        snippet = re.sub(r"<[^>]+>", "", desc)[:300]
        articles.append({"title": title, "snippet": snippet, "link": link})
    return articles

# ─── Claude post generation ───────────────────────────────────────────────────
def generate_posts(company, articles):
    if not articles:
        return None

    news_text = "\n\n".join(
        f"Title: {a['title']}\nSnippet: {a['snippet']}"
        for a in articles
    )

    prompt = f"""{PERSONA}

Here is recent news about {company}:

{news_text}

Write 3 different LinkedIn post options for Shelle about {company} based on this news.
Each should be a different angle:

1. [DEAL OBSERVATION] — What this means for M&A activity, corp dev, or dealmakers
2. [SEASONED POV] — Shelle's direct take after 2,300+ deals. Opinionated. Short.
3. [HUMAN/UNEXPECTED] — A personal story or watercolor/art analogy that ties back to the news

Format exactly like this:
---
[DEAL OBSERVATION]
(post text)

---
[SEASONED POV]
(post text)

---
[HUMAN/UNEXPECTED]
(post text)

No intro text. No explanations. Just the three posts."""

    try:
        msg = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        print(f"  ERROR: Claude failed for {company}: {e}")
        return None

# ─── Email ────────────────────────────────────────────────────────────────────
def send_email(body_text, output_path):
    # Save to file
    with open(output_path, "w") as f:
        f.write(f"LinkedIn Brief — {TODAY} {datetime.now().strftime('%H:%M')}\n\n")
        f.write(body_text)
    print(f"  Saved to {output_path}")

    intro = MONDAY_INTRO if DAY == "Monday" else THURSDAY_INTRO
    subject = f"LinkedIn Brief — {DAY} {TODAY}"

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:'Segoe UI',Arial,sans-serif;max-width:680px;margin:0 auto;padding:24px;color:#1f2937;">
<div style="background:linear-gradient(135deg,#1e3a5f,#2d6a9f);padding:24px 28px;border-radius:10px 10px 0 0;">
  <h1 style="color:#fff;margin:0;font-size:20px;">LinkedIn Brief — {DAY}, {TODAY}</h1>
  <p style="color:#bfdbfe;margin:8px 0 0;font-size:14px;">Shelle Martin | Datasite</p>
</div>
<div style="background:#fff;border:1px solid #e5e7eb;border-top:none;padding:24px 28px;border-radius:0 0 10px 10px;">
  <p style="color:#6b7280;font-size:14px;">{intro.strip()}</p>
  <hr style="border:none;border-top:1px solid #e5e7eb;margin:20px 0;">
  <pre style="white-space:pre-wrap;font-family:'Segoe UI',Arial,sans-serif;font-size:14px;line-height:1.7;color:#1f2937;">{body_text}</pre>
  <hr style="border:none;border-top:1px solid #e5e7eb;margin:20px 0;">
  <p style="font-size:12px;color:#9ca3af;">ShelleOS | Next brief: {'Thursday' if DAY == 'Monday' else 'Monday'}</p>
</div>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["From"] = GMAIL_FROM
    msg["To"] = GMAIL_TO
    msg["Subject"] = subject
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_FROM, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        print(f"  Email sent to {GMAIL_TO}")
    except Exception as e:
        print(f"  ERROR sending email: {e}")

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    print(f"\nLinkedIn Brief — Shelle — {TODAY} ({DAY})")
    print("=" * 60)

    all_posts = []

    for company in COMPANIES:
        print(f"\n{company}...")
        articles = fetch_news(company)
        print(f"  Found {len(articles)} articles")
        if not articles:
            continue
        posts = generate_posts(company, articles)
        if posts:
            all_posts.append(f"{'='*60}\n{company.upper()}\n{'='*60}\n\n{posts}")
            print(f"  Generated posts")

    if not all_posts:
        print("No posts generated.")
        return

    body = "\n\n".join(all_posts)
    output_path = os.path.join(SCRIPT_DIR, "linkedin_brief_output.txt")
    send_email(body, output_path)
    print(f"\nDone. {len(all_posts)} companies covered.")

if __name__ == "__main__":
    main()
