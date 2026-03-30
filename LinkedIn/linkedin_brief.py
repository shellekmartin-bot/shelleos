#!/usr/bin/env python3
"""
LinkedIn Brief Generator
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
import anthropic

# ─── Load environment ────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(SCRIPT_DIR, '..', '.env'))

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

GMAIL_FROM = os.getenv("GMAIL_FROM")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
SEND_TO = "shelle.martin@datasite.com"

missing = [k for k, v in {
    "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
    "GMAIL_FROM": GMAIL_FROM,
    "GMAIL_APP_PASSWORD": GMAIL_APP_PASSWORD,
}.items() if not v]
if missing:
    print(f"ERROR: Missing .env keys: {', '.join(missing)}")
    sys.exit(1)

# ─── Persona (swap this block for a different person) ────────────────────────
PERSONA = """
Name: Shelle Martin
Background: 20 years in Bay Area M&A, 2300+ deals closed, Peace Corps, watercolor artist, AI-forward operator. Last person you'd expect to be this tech-forward.
Territory: Bay Area, enterprise corp dev, GC, CFO
Voice: blunt, GenX, peer-to-peer, dry wit, short sentences, ellipses for pacing
"""

# ─── Voice rules (same for all personas) ─────────────────────────────────────
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
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def load_companies():
    """Load companies from companies.txt. Format: Company Name | LinkedIn URL (optional)"""
    path = os.path.join(SCRIPT_DIR, "companies.txt")
    if not os.path.exists(path):
        print(f"ERROR: {path} not found. Create it with one company per line.")
        sys.exit(1)
    companies = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "|" in line:
                name, url = line.split("|", 1)
                companies.append({"name": name.strip(), "linkedin_url": url.strip()})
            else:
                companies.append({"name": line, "linkedin_url": ""})
    if not companies:
        print("ERROR: companies.txt is empty.")
        sys.exit(1)
    print(f"Loaded {len(companies)} companies from companies.txt")
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
            model="claude-sonnet-4-20250514",
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        print(f"  WARNING: Claude generation failed for {company_name}: {e}")
        return None


def send_email(body_text):
    today = datetime.now().strftime("%Y-%m-%d")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"LinkedIn Brief — {today}"
    msg["From"] = GMAIL_FROM
    msg["To"] = SEND_TO
    html = "<html><body><pre style='font-family: monospace; font-size: 14px;'>"
    html += body_text.replace("<", "&lt;").replace(">", "&gt;")
    html += "</pre></body></html>"
    msg.attach(MIMEText(body_text, "plain"))
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_FROM, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_FROM, SEND_TO, msg.as_string())
        print(f"Email sent to {SEND_TO}")
    except Exception as e:
        print(f"WARNING: Email failed: {e}")


def main():
    print(f"LinkedIn Brief Generator — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    companies = load_companies()
    output_path = os.path.join(SCRIPT_DIR, "linkedin_brief_output.txt")
    results = []

    for i, acct in enumerate(companies, 1):
        name = acct["name"]
        linkedin_url = acct["linkedin_url"]
        print(f"\n[{i}/{len(companies)}] {name}")
        print(f"  Searching recent news...")
        news = search_recent_news(name)
        if not news:
            print(f"  Skipping {name} — no news found.")
            results.append(f"\n{'=' * 60}\n{name}\n{'=' * 60}\nNo recent news found. Skipped.\n")
            continue

        print(f"  Generating LinkedIn angles...")
        angles = generate_linkedin_angles(name, news)
        if not angles:
            print(f"  Skipping {name} — generation failed.")
            results.append(f"\n{'=' * 60}\n{name}\n{'=' * 60}\nGeneration failed.\n")
            continue

        url_line = f"LinkedIn: {linkedin_url}\n" if linkedin_url else ""
        block = f"\n{'=' * 60}\n{name.upper()}\n{'=' * 60}\n{url_line}\n{angles}\n"
        results.append(block)
        print(block)

        if i < len(companies):
            time.sleep(1)

    full_output = f"LinkedIn Brief — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n" + "".join(results)
    with open(output_path, "w") as f:
        f.write(full_output)
    print(f"\nSaved to {output_path}")

    send_email(full_output)


if __name__ == "__main__":
    main()
