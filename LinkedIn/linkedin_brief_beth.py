#!/usr/bin/env python3
"""
LinkedIn Brief Generator — Beth's version
Fetches recent news via Google News RSS (free), generates 3 LinkedIn
post angles per company using Claude.
"""

import os, sys, re, time, requests, smtplib
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from urllib.parse import quote_plus
from dotenv import load_dotenv
import httpx
import anthropic

# ─── Load environment ────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(SCRIPT_DIR, '..', '.env'), override=True)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

GMAIL_FROM = os.getenv("GMAIL_FROM")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
SEND_TO = "beth.shugart@datasite.com"

missing = [k for k, v in {
    "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
    "GMAIL_FROM": GMAIL_FROM,
    "GMAIL_APP_PASSWORD": GMAIL_APP_PASSWORD,
}.items() if not v]
if missing:
    print(f"ERROR: Missing .env keys: {', '.join(missing)}")
    sys.exit(1)

# ─── Persona ─────────────────────────────────────────────────────────────────
PERSONA = """
Name: Beth Shugart
Background: 15+ years of M&A execution at some of the largest F500s in the Valley. Did deals at Cisco, Intel, Sun Microsystems. She's been on the other side of the table... she knows what corp dev actually needs because she was corp dev. San Francisco native, lives locally. Now in enterprise sales bringing the operator's perspective.
Territory: San Jose. Covers the biggest tech acquirers in Silicon Valley.
Voice: senior, credible, direct, practitioner who's done the work
"""

# ─── Voice rules ─────────────────────────────────────────────────────────────
VOICE_RULES = """
- Lead with the point. No warmup.
- Short sentences. Ellipses for pacing.
- No em dashes. Ever.
- No buzzwords, no consultant-speak.
- 1 hashtag max.
- 4-5 sentences per post. iPhone scroll length.
- Sounds like a person, not a vendor.
"""

# ─── API clients ─────────────────────────────────────────────────────────────
http_client = httpx.Client()
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, http_client=http_client)


def load_companies():
    path = os.path.join(SCRIPT_DIR, "companies_beth.txt")
    if not os.path.exists(path):
        print(f"ERROR: {path} not found. Create it with one company name per line.")
        sys.exit(1)
    with open(path) as f:
        companies = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    if not companies:
        print("ERROR: companies_beth.txt is empty.")
        sys.exit(1)
    print(f"Loaded {len(companies)} companies from companies_beth.txt")
    return companies


def search_recent_news(company_name):
    """Fetch recent news from Google News RSS (free). Returns summary string or None."""
    encoded = quote_plus(company_name)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    try:
        resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        print(f"  WARNING: Google News fetch failed for {company_name}: {e}")
        return None

    cutoff = datetime.now() - timedelta(days=30)
    articles = []
    for item in root.findall(".//item")[:10]:
        title = item.findtext("title") or ""
        description = item.findtext("description") or ""
        pub_date_str = item.findtext("pubDate") or ""
        try:
            pub_date = parsedate_to_datetime(pub_date_str).replace(tzinfo=None)
            if pub_date < cutoff:
                continue
            date_str = pub_date.strftime("%Y-%m-%d")
        except Exception:
            date_str = datetime.now().strftime("%Y-%m-%d")
        snippet = re.sub(r"<[^>]+>", "", description)[:300]
        articles.append(f"[{date_str}] {title} -- {snippet}")

    if not articles:
        return None
    return "\n".join(articles)


def generate_linkedin_angles(company_name, news_summary):
    prompt = f"""You are ghostwriting LinkedIn posts for this person:

{PERSONA}

Here is recent news about {company_name}:
{news_summary}

Write exactly 3 LinkedIn post drafts about {company_name}. Each post must follow these voice rules:
{VOICE_RULES}

The 3 posts must be one of each type:

1. DEAL OBSERVATION: A pattern this person is seeing across their territory. Start with "Seeing a lot of..." or similar. Connect the company news to a broader trend. This is a peer talking to peers about what they are noticing.

2. SEASONED POV: Short, sharp, opinionated take. Something only someone with this person's background could say. Not advice. A take. The kind of thing that makes people stop scrolling because someone finally said it.

3. HUMAN/UNEXPECTED: What makes this person memorable versus every other vendor in the feed. Could tie the company news to something personal, a conversation in a hallway, a moment that stuck. The angle nobody else would write.

Format each post like this:

---
[DEAL OBSERVATION]
[the post text, 4-5 sentences]

---
[SEASONED POV]
[the post text, 4-5 sentences]

---
[HUMAN/UNEXPECTED]
[the post text, 4-5 sentences]
---

Do not add commentary, explanations, or options. Just the 3 posts."""

    try:
        msg = claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        print(f"  WARNING: Claude generation failed for {company_name}: {e}")
        return None


def main():
    print(f"LinkedIn Brief Generator (Beth) — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    companies = load_companies()
    output_path = os.path.join(SCRIPT_DIR, "linkedin_brief_beth_output.txt")
    results = []

    for i, company in enumerate(companies, 1):
        print(f"\n[{i}/{len(companies)}] {company}")
        print(f"  Searching recent news...")
        news = search_recent_news(company)
        if not news:
            print(f"  Skipping {company} — no news found.")
            results.append(f"\n{'=' * 60}\n{company}\n{'=' * 60}\nNo recent news found. Skipped.\n")
            continue

        print(f"  Generating LinkedIn angles...")
        angles = generate_linkedin_angles(company, news)
        if not angles:
            print(f"  Skipping {company} — generation failed.")
            results.append(f"\n{'=' * 60}\n{company}\n{'=' * 60}\nGeneration failed.\n")
            continue

        block = f"\n{'=' * 60}\n{company.upper()}\n{'=' * 60}\n\n{angles}\n"
        results.append(block)
        print(block)

        if i < len(companies):
            time.sleep(1)

    full_output = f"LinkedIn Brief (Beth) — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n" + "".join(results)
    with open(output_path, "w") as f:
        f.write(full_output)
    print(f"\nSaved to {output_path}")

    send_email(full_output)


def send_email(body_text):
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    is_thursday = now.weekday() == 3

    if is_thursday:
        intro = """Hey Beth — Thursday round 2.

If you posted Monday... you're ahead of the game. If you didn't... that's what today is for.

3 angles per company. Pick one, tweak it, post it. Takes 2 minutes.
─────────────────────────────────────────────────────────────

"""
    else:
        intro = """Hey Beth — Happy Monday.

Fresh LinkedIn angles for your San Jose accounts based on what's moving right now.

3 angles per company:
  Deal Observation — what you're seeing across the territory
  Seasoned POV — the take only someone with your background can make
  Human/Unexpected — the one that makes them remember you

Pick the ones that hit, tweak if you want, and post.
─────────────────────────────────────────────────────────────

"""
    full_text = intro + body_text
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Your LinkedIn Ammo — {today}"
    msg["From"] = GMAIL_FROM
    msg["To"] = SEND_TO
    html = "<html><body><pre style='font-family: monospace; font-size: 14px;'>"
    html += full_text.replace("<", "&lt;").replace(">", "&gt;")
    html += "</pre></body></html>"
    msg.attach(MIMEText(full_text, "plain"))
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_FROM, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_FROM, SEND_TO, msg.as_string())
        print(f"Email sent to {SEND_TO}")
    except Exception as e:
        print(f"WARNING: Email failed: {e}")


if __name__ == "__main__":
    main()
