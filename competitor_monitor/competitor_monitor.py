#!/usr/bin/env python3
"""
Competitor Intelligence Monitor — Intralinks & Venue
Fetches signals from Google News RSS + Reddit, classifies with Claude Haiku,
deduplicates via SQLite, and sends daily digest email at 6 AM Pacific.

Usage:
  python3 competitor_monitor.py    # Runs full pipeline

.env requires: ANTHROPIC_API_KEY, GMAIL_FROM, GMAIL_APP_PASSWORD
"""

import os
import sys
import json
import re
import sqlite3
import hashlib
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, date
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from dotenv import load_dotenv
import httpx
import anthropic

# ─── Credentials ──────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(SCRIPT_DIR, '..', '.env'), override=True)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GMAIL_FROM = os.getenv("GMAIL_FROM")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

missing = [k for k, v in {
    "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
    "GMAIL_FROM": GMAIL_FROM,
    "GMAIL_APP_PASSWORD": GMAIL_APP_PASSWORD,
}.items() if not v]
if missing:
    print(f"\nERROR: Missing env vars: {', '.join(missing)}")
    sys.exit(1)

# Create httpx client without proxy interference
http_client = httpx.Client()
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, http_client=http_client)
TODAY = date.today().isoformat()

# ─── Database setup ───────────────────────────────────────────────────────────
DB_PATH = os.path.join(SCRIPT_DIR, "data", "signals.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def init_db():
    """Create signals table if it doesn't exist."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            content_hash TEXT PRIMARY KEY,
            competitor TEXT,
            source TEXT,
            headline TEXT,
            url TEXT,
            snippet TEXT,
            signal_type TEXT,
            rank INTEGER,
            published_date TEXT,
            discovered_date TEXT,
            emailed TEXT,
            why_relevant TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT,
            signals_found INTEGER,
            signals_new INTEGER,
            errors TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# ─── Google News RSS ───────────────────────────────────────────────────────────
def fetch_google_news(query, lookback_days=7):
    """Fetch from Google News RSS. Returns list of articles."""
    encoded = quote_plus(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    try:
        resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        print(f"  WARNING: Google News fetch failed for '{query}': {e}")
        return []

    cutoff = datetime.now() - timedelta(days=lookback_days)
    articles = []
    for item in root.findall(".//item")[:15]:
        title = item.findtext("title") or ""
        description = item.findtext("description") or ""
        pub_date_str = item.findtext("pubDate") or ""
        link = item.findtext("link") or ""
        try:
            pub_date = parsedate_to_datetime(pub_date_str).replace(tzinfo=None)
            if pub_date < cutoff:
                continue
            date_str = pub_date.strftime("%Y-%m-%d")
        except Exception:
            date_str = TODAY
        snippet = re.sub(r"<[^>]+>", "", description)[:300]
        articles.append({"title": title, "snippet": snippet, "date_str": date_str, "link": link})
    return articles

# ─── Reddit integration ───────────────────────────────────────────────────────
def fetch_reddit_posts(subreddit_name, search_term, limit=10):
    """Fetch posts from a subreddit mentioning search term. Returns list of posts."""
    url = f"https://www.reddit.com/r/{subreddit_name}/search.json?q={quote_plus(search_term)}&restrict_sr=on&sort=new&limit={limit}"
    headers = {"User-Agent": "CompetitorMonitor/1.0"}
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        posts = []
        for item in data.get("data", {}).get("children", []):
            post = item.get("data", {})
            posts.append({
                "title": post.get("title", ""),
                "snippet": post.get("selftext", "")[:300],
                "url": f"https://reddit.com{post.get('permalink', '')}",
                "date_str": datetime.fromtimestamp(post.get("created_utc", 0)).strftime("%Y-%m-%d"),
                "subreddit": subreddit_name,
            })
        return posts
    except Exception as e:
        print(f"  WARNING: Reddit fetch failed for r/{subreddit_name}: {e}")
        return []

# ─── Claude Haiku classification ──────────────────────────────────────────────
def classify_signals(competitor_name, articles):
    """Send articles to Claude Haiku for signal classification. Returns list of signals."""
    if not articles:
        return []

    articles_text = "\n\n".join(
        f"Date: {a['date_str']}\nTitle: {a['title']}\nSnippet: {a['snippet']}\nURL: {a.get('link') or a.get('url', '')}"
        for a in articles
    )

    prompt = f"""Analyze these news articles about {competitor_name} and extract competitive signals.

Articles:
{articles_text}

Return a JSON array of signals that matter to a VDR/M&A platform competitor (Datasite):
- New features or product launches (esp. AI, collaboration, security)
- Pricing changes or new pricing tiers
- Leadership changes (C-suite, product team)
- Compliance or regulatory updates
- Major partnerships or integrations
- Significant announcements

Skip: minor press releases, sponsorships, analyst coverage, stock price movement, historical news.
Max 3 signals. If nothing significant, return [].

JSON only, no markdown:
[
  {{
    "headline": "one-line summary under 80 chars",
    "signal_type": "feature|pricing|employee|compliance|partnership|news",
    "rank": 1-5 (1=critical, 5=minor),
    "why_relevant": "one sentence explaining competitive impact",
    "source_url": "best link for this signal"
  }}
]"""

    try:
        msg = claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        if "[" in text:
            return json.loads(text[text.index("["):text.rindex("]") + 1])
    except Exception as e:
        print(f"  ERROR: Claude classification failed: {e}")
    return []

# ─── Deduplication ────────────────────────────────────────────────────────────
def signal_hash(competitor, source, headline):
    """Generate SHA256 hash of signal for dedup."""
    content = f"{competitor}|{source}|{headline}".encode()
    return hashlib.sha256(content).hexdigest()

def is_new_signal(content_hash):
    """Check if signal hash already exists in DB."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM signals WHERE content_hash = ?", (content_hash,))
    exists = c.fetchone() is not None
    conn.close()
    return not exists

def store_signal(competitor, source, headline, url, snippet, signal_type, rank, pub_date, why_relevant):
    """Store signal in SQLite."""
    content_hash = signal_hash(competitor, source, headline)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO signals
            (content_hash, competitor, source, headline, url, snippet, signal_type, rank, published_date, discovered_date, why_relevant)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (content_hash, competitor, source, headline, url, snippet, signal_type, rank, pub_date, TODAY, why_relevant))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def get_new_signals():
    """Fetch all signals that haven't been emailed yet."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM signals WHERE emailed IS NULL ORDER BY competitor, rank")
    rows = c.fetchall()
    conn.close()

    signals = []
    for row in rows:
        signals.append({
            "content_hash": row[0],
            "competitor": row[1],
            "source": row[2],
            "headline": row[3],
            "url": row[4],
            "snippet": row[5],
            "signal_type": row[6],
            "rank": row[7],
            "published_date": row[8],
            "why_relevant": row[11],
        })
    return signals

def mark_signals_emailed(hashes):
    """Mark signals as emailed."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    timestamp = datetime.now().isoformat()
    for h in hashes:
        c.execute("UPDATE signals SET emailed = ? WHERE content_hash = ?", (timestamp, h))
    conn.commit()
    conn.close()

# ─── Main collector ───────────────────────────────────────────────────────────
def collect_signals():
    """Collect all new signals for both competitors."""
    new_signals = []

    # ── Intralinks ────────────────────────────────────────────────────────────
    print("\n📊 Intralinks / SS&C")
    print("-" * 60)

    # Google News RSS
    queries = [
        '"Intralinks" new feature OR announcement OR update',
        '"SS&C" Intralinks OR "Intralinks" pricing OR employee OR CEO',
    ]

    for query in queries:
        articles = fetch_google_news(query, lookback_days=1)
        if articles:
            print(f"  Found {len(articles)} articles for: {query[:50]}...")
            signals = classify_signals("Intralinks", articles)
            for sig in signals:
                headline = sig.get("headline", "")
                h = signal_hash("Intralinks", "google_news", headline)
                if is_new_signal(h):
                    store_signal(
                        "Intralinks", "google_news", headline,
                        sig.get("source_url", ""),
                        sig.get("snippet", "")[:200],
                        sig.get("signal_type", "news"),
                        sig.get("rank", 5),
                        datetime.now().strftime("%Y-%m-%d"),
                        sig.get("why_relevant", "")
                    )
                    print(f"  ✅ {headline}")
                    new_signals.append(("Intralinks", headline))

    # Reddit discussion
    print("\n  Checking Reddit (r/M-and-A, r/legal-tech, r/datasite)...")
    reddit_subs = ["M_and_A", "legal_tech"]
    for subreddit in reddit_subs:
        posts = fetch_reddit_posts(subreddit, "Intralinks", limit=10)
        if posts:
            print(f"    Found {len(posts)} posts in r/{subreddit}")
            signals = classify_signals("Intralinks", posts)
            for sig in signals:
                headline = sig.get("headline", "")
                h = signal_hash("Intralinks", "reddit", headline)
                if is_new_signal(h):
                    store_signal(
                        "Intralinks", "reddit", headline,
                        sig.get("source_url", ""),
                        sig.get("snippet", "")[:200],
                        sig.get("signal_type", "news"),
                        sig.get("rank", 5),
                        datetime.now().strftime("%Y-%m-%d"),
                        sig.get("why_relevant", "")
                    )
                    print(f"    ✅ {headline}")
                    new_signals.append(("Intralinks", headline))

    # ── Venue ──────────────────────────────────────────────────────────────────
    print("\n📊 Venue / RRDonelly")
    print("-" * 60)

    queries = [
        '"Venue" VDR OR "Venue" new feature OR announcement',
        '"RRDonelly" Venue OR "Venue" pricing OR employee OR CEO',
    ]

    for query in queries:
        articles = fetch_google_news(query, lookback_days=1)
        if articles:
            print(f"  Found {len(articles)} articles for: {query[:50]}...")
            signals = classify_signals("Venue", articles)
            for sig in signals:
                headline = sig.get("headline", "")
                h = signal_hash("Venue", "google_news", headline)
                if is_new_signal(h):
                    store_signal(
                        "Venue", "google_news", headline,
                        sig.get("source_url", ""),
                        sig.get("snippet", "")[:200],
                        sig.get("signal_type", "news"),
                        sig.get("rank", 5),
                        datetime.now().strftime("%Y-%m-%d"),
                        sig.get("why_relevant", "")
                    )
                    print(f"  ✅ {headline}")
                    new_signals.append(("Venue", headline))

    # Reddit discussion
    print("\n  Checking Reddit (r/M-and-A, r/legal-tech, r/datasite)...")
    reddit_subs = ["M_and_A", "legal_tech"]
    for subreddit in reddit_subs:
        posts = fetch_reddit_posts(subreddit, "Venue", limit=10)
        if posts:
            print(f"    Found {len(posts)} posts in r/{subreddit}")
            signals = classify_signals("Venue", posts)
            for sig in signals:
                headline = sig.get("headline", "")
                h = signal_hash("Venue", "reddit", headline)
                if is_new_signal(h):
                    store_signal(
                        "Venue", "reddit", headline,
                        sig.get("source_url", ""),
                        sig.get("snippet", "")[:200],
                        sig.get("signal_type", "news"),
                        sig.get("rank", 5),
                        datetime.now().strftime("%Y-%m-%d"),
                        sig.get("why_relevant", "")
                    )
                    print(f"    ✅ {headline}")
                    new_signals.append(("Venue", headline))

    return new_signals

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*60}")
    print(f"Competitor Intelligence Monitor — {TODAY}")
    print(f"{'='*60}")

    new_signals = collect_signals()

    print(f"\n{'='*60}")
    print(f"Total new signals: {len(new_signals)}")

    if new_signals:
        print("\nCalling email_digest.py to send email...")
        os.system(f"cd {SCRIPT_DIR} && python3 email_digest.py")
    else:
        print("No new signals — skipping email.")

    print(f"Done.\n")

if __name__ == "__main__":
    main()
