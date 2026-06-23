#!/usr/bin/env python3
"""
Account News Monitor — ShelleOS
Scans Google News RSS for deal signals across a rep's territory accounts,
classifies with Claude Haiku, emails a daily brief.

NO Airtable. NO Google Sheets API. Fully standalone.

Usage:
  python3 account_news_monitor.py --accounts-file shelleOS/accounts/john_stallings.txt --recipient John.stallings@datasite.com
  python3 account_news_monitor.py --accounts-file shelleOS/accounts/john_stallings.txt --recipient John.stallings@datasite.com --dry-run

.env requires: ANTHROPIC_API_KEY, GMAIL_APP_PASSWORD, GMAIL_FROM
"""

import os
import sys
import json
import re
import time
import smtplib
import argparse
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from dotenv import load_dotenv
import anthropic

# ─── Credentials ──────────────────────────────────────────────────────────────
load_dotenv()

ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
GMAIL_FROM         = os.getenv("GMAIL_FROM", "shelle.k.martin@gmail.com")

missing = [k for k, v in {
    "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
    "GMAIL_APP_PASSWORD": GMAIL_APP_PASSWORD,
}.items() if not v]
if missing:
    print(f"\nERROR: Missing env vars: {', '.join(missing)}")
    sys.exit(1)

claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
TODAY = date.today().isoformat()


# ─── Company list loader ───────────────────────────────────────────────────────
def load_companies(accounts_file):
    if not os.path.exists(accounts_file):
        print(f"ERROR: Accounts file not found: {accounts_file}")
        sys.exit(1)
    with open(accounts_file) as f:
        companies = [line.strip() for line in f
                     if line.strip() and not line.startswith("#")]
    if not companies:
        print(f"ERROR: {accounts_file} is empty.")
        sys.exit(1)
    print(f"Loaded {len(companies)} companies from {accounts_file}")
    return companies


# ─── Signal types ──────────────────────────────────────────────────────────────
TYPE_ACQUISITION = "M&A"
TYPE_SELL_SIDE   = "Sell Side"
TYPE_ACTIVIST    = "Activist"
TYPE_FUNDING     = "Funding"
TYPE_LEADERSHIP  = "Leadership"
TYPE_PARTNERSHIP = "Partnership"
TYPE_LAYOFFS     = "Layoffs"
TYPE_EARNINGS    = "Earnings"
TYPE_NEWS        = "News"


def classify_signal(signal_type_str):
    s = (signal_type_str or "").lower().strip()
    if any(x in s for x in ["acqui", "merger", "divest", "buyout", "spac", "pe invest"]):
        return TYPE_ACQUISITION
    if any(x in s for x in ["sell_side", "sell side", "strategic review", "exploring sale"]):
        return TYPE_SELL_SIDE
    if any(x in s for x in ["activist", "elliott", "starboard", "valueact", "jana",
                              "icahn", "third point", "pershing", "engaged capital",
                              "takes stake", "proxy fight", "board seats", "shareholder letter"]):
        return TYPE_ACTIVIST
    if any(x in s for x in ["fund", "ipo", "round", "capital raise", "debt", "series", "credit"]):
        return TYPE_FUNDING
    if any(x in s for x in ["leadership", "ceo", "cfo", "clo", "coo", "chief",
                              "president", "appoint", "hire", "depart", "resign", "board"]):
        return TYPE_LEADERSHIP
    if any(x in s for x in ["partner", "joint venture", "jv", "alliance", "integrat"]):
        return TYPE_PARTNERSHIP
    if any(x in s for x in ["layoff", "cut", "reduct", "workforce"]):
        return TYPE_LAYOFFS
    if any(x in s for x in ["earn", "quarterly", "revenue", "q1", "q2", "q3", "q4",
                              "fiscal", "guidance", "results", "restructur"]):
        return TYPE_EARNINGS
    return TYPE_NEWS


def rank_signal(trigger_type, headline, notes):
    h = (headline + " " + (notes or "")).lower()
    billions = any(x in h for x in ["billion", " $1b", " $2b", " $3b", " $5b", " $10b"])
    if trigger_type == TYPE_ACQUISITION and billions:
        return 1
    if trigger_type in (TYPE_ACQUISITION, TYPE_SELL_SIDE):
        return 2
    if trigger_type == TYPE_ACTIVIST:
        return 3
    if trigger_type == TYPE_FUNDING:
        return 4
    if trigger_type == TYPE_LEADERSHIP:
        return 5
    if trigger_type == TYPE_PARTNERSHIP:
        return 6
    if trigger_type == TYPE_LAYOFFS:
        return 7
    if trigger_type == TYPE_EARNINGS:
        return 8
    return 9


# ─── Google News RSS ───────────────────────────────────────────────────────────
def fetch_google_news(query, lookback_days=7):
    encoded = quote_plus(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    try:
        resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception:
        return []

    cutoff   = datetime.now() - timedelta(days=lookback_days)
    articles = []
    for item in root.findall(".//item")[:12]:
        title        = item.findtext("title") or ""
        description  = item.findtext("description") or ""
        pub_date_str = item.findtext("pubDate") or ""
        link         = item.findtext("link") or ""
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


def classify_articles_with_haiku(articles, context_query):
    if not articles:
        return []
    articles_text = "\n\n".join(
        f"Date: {a['date_str']}\nTitle: {a['title']}\nSnippet: {a['snippet']}"
        for a in articles
    )
    prompt = f"""Extract deal signals from these news articles. Context: {context_query}

Articles:
{articles_text}

Return a JSON array of real deal signals only:
- Acquisitions, mergers, divestitures, PE buyouts
- Funding rounds, IPO filings, debt raises
- Leadership changes (CEO, CFO, GC, Corp Dev)
- Earnings with M&A or strategic commentary
- Strategic review / exploring sale
- Activist investor taking a stake or pushing for change
- Significant partnerships (JVs, distribution deals)
- Layoffs or major restructurings

Skip: press releases, product updates, awards, analyst ratings, stock price moves, historical news (anything older than current week), minor partnerships like sponsorships or charity deals.
Max 2 signals. If nothing real, return [].

JSON only, no markdown:
[
  {{
    "company_name": "exact company name from articles",
    "headline": "one-line summary under 100 chars",
    "signal_type": "acquisition|sell_side|activist|funding|leadership|partnership|layoffs|earnings|news",
    "notes": "2-3 sentences: what happened and why it matters",
    "date_str": "YYYY-MM-DD"
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
    except Exception:
        pass
    return []


def google_news_search_signals(query, lookback_days=7):
    articles = fetch_google_news(query, lookback_days=lookback_days)
    if not articles:
        return []
    return classify_articles_with_haiku(articles, query)


# ─── Signal collector ─────────────────────────────────────────────────────────
def match_company(name, company_names_set):
    """Fuzzy match — handles slight name variations from Claude."""
    n = name.lower().strip()
    if n in company_names_set:
        return True
    for c in company_names_set:
        if n in c or c in n:
            return True
    return False


class SignalCollector:
    def __init__(self, company_names_set):
        self.company_names_set     = company_names_set
        self.signals               = []
        self.seen_headlines        = set()
        self.company_signal_counts = {}

    def add(self, signal):
        company_name = (signal.get("company_name") or "").strip()
        headline     = (signal.get("headline") or "").strip()
        notes        = (signal.get("notes") or "").strip()
        signal_type  = signal.get("signal_type", "news")
        date_str     = signal.get("date_str", TODAY)

        if not headline or not company_name:
            return None

        if not match_company(company_name, self.company_names_set):
            return None

        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            date_str = TODAY

        h_short = headline.lower()[:40]
        if h_short in self.seen_headlines:
            return None
        self.seen_headlines.add(h_short)

        key = company_name.lower()
        if self.company_signal_counts.get(key, 0) >= 2:
            return None

        trigger_type = classify_signal(signal_type)
        rank         = rank_signal(trigger_type, headline, notes)

        entry = {
            "company_name": company_name,
            "headline":     headline,
            "notes":        notes,
            "trigger_type": trigger_type,
            "date_str":     date_str,
            "rank":         rank,
            "link":         signal.get("link", ""),
        }
        self.signals.append(entry)
        self.company_signal_counts[key] = self.company_signal_counts.get(key, 0) + 1
        return entry


# ─── Badge colors per signal type ─────────────────────────────────────────────
BADGE_COLORS = {
    TYPE_ACQUISITION: ("#c0392b", "M&A"),
    TYPE_SELL_SIDE:   ("#8e44ad", "Sell Side"),
    TYPE_ACTIVIST:    ("#d35400", "Activist"),
    TYPE_FUNDING:     ("#27ae60", "Funding / IPO"),
    TYPE_LEADERSHIP:  ("#2980b9", "Leadership"),
    TYPE_PARTNERSHIP: ("#16a085", "Partnership"),
    TYPE_LAYOFFS:     ("#e67e22", "Layoffs"),
    TYPE_EARNINGS:    ("#5d6d7e", "Earnings"),
    TYPE_NEWS:        ("#7f8c8d", "News"),
}

SECTION_LABELS = [
    (TYPE_ACQUISITION, "M&A / Acquisitions"),
    (TYPE_SELL_SIDE,   "Sell Side / Strategic Review"),
    (TYPE_ACTIVIST,    "Activist"),
    (TYPE_FUNDING,     "Funding / IPO"),
    (TYPE_LEADERSHIP,  "Leadership Changes"),
    (TYPE_PARTNERSHIP, "Partnerships"),
    (TYPE_LAYOFFS,     "Layoffs / Restructuring"),
    (TYPE_EARNINGS,    "Earnings"),
    (TYPE_NEWS,        "Other News"),
]


def _badge(ttype):
    color, label = BADGE_COLORS.get(ttype, ("#7f8c8d", ttype))
    return (
        f'<span style="background:{color};color:#fff;font-size:11px;font-weight:700;'
        f'letter-spacing:0.5px;padding:3px 9px;border-radius:3px;'
        f'font-family:Arial,sans-serif;text-transform:uppercase;">{label}</span>'
    )


def _esc(text):
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_brief_html(signals, num_accounts, dry_run=False, datasite_brand=False):
    signals  = sorted(signals, key=lambda s: s["rank"])
    total    = len(signals)
    tag      = " · TEST" if dry_run else ""
    date_fmt = datetime.strptime(TODAY, "%Y-%m-%d").strftime("%A, %B %-d, %Y")

    # ── Theme: Datasite brand vs default ──────────────────────────────────────
    if datasite_brand:
        outer_bg     = "#E4E3E8"   # Datasite Light Gray
        header_bg    = "#78737D"   # Datasite Dark Gray
        header_sub   = "#E4E3E8"
        accent       = "#FF9F27"   # Datasite Orange
        body_text    = "#575559"   # Datasite Text Gray
        card_head_bg = "#F5F5F5"
        card_border  = "#E4E3E8"
        headline_clr = "#575559"
        section_line = "#FF9F27"
        section_clr  = "#78737D"
        footer_clr   = "#78737D"
        link_clr     = "#575559"
    else:
        outer_bg     = "#f0f2f5"
        header_bg    = "#1a2332"
        header_sub   = "#8899aa"
        accent       = "#ffffff"
        body_text    = "#555"
        card_head_bg = "#f8f9fb"
        card_border  = "#e8ecf0"
        headline_clr = "#1a2332"
        section_line = None        # uses per-type badge color
        section_clr  = None
        footer_clr   = "#aaa"
        link_clr     = "#1a2332"

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:{outer_bg};font-family:Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:{outer_bg};">
<tr><td align="center" style="padding:24px 12px;">
<table width="680" cellpadding="0" cellspacing="0" style="max-width:680px;width:100%;">

  <!-- HEADER -->
  <tr><td style="background:{header_bg};border-radius:8px 8px 0 0;padding:28px 32px;{'border-bottom:4px solid ' + accent + ';' if datasite_brand else ''}">
    <div style="color:#fff;font-size:26px;font-weight:700;letter-spacing:-0.5px;font-family:Arial,sans-serif;">
      Account Intelligence Brief{tag}
    </div>
    <div style="color:{header_sub};font-size:14px;margin-top:6px;font-family:Arial,sans-serif;">
      {date_fmt} &nbsp;|&nbsp; {num_accounts} accounts monitored &nbsp;|&nbsp;
      <strong style="color:{accent};">{total} signal{'s' if total != 1 else ''} today</strong>
    </div>
  </td></tr>

  <!-- BODY -->
  <tr><td style="background:#fff;border-radius:0 0 8px 8px;padding:28px 32px;">
"""

    if not signals:
        html += (
            f'<p style="color:{body_text};font-size:15px;font-family:Arial,sans-serif;">'
            f'Quiet day — no significant signals found across your {num_accounts} accounts.</p>'
        )
    else:
        for ttype, section_label in SECTION_LABELS:
            items = [s for s in signals if s["trigger_type"] == ttype]
            if not items:
                continue

            badge_color, _ = BADGE_COLORS.get(ttype, ("#7f8c8d", ttype))
            divider_color  = section_line if datasite_brand else badge_color
            label_color    = section_clr  if datasite_brand else badge_color

            html += f"""
    <!-- SECTION: {section_label} -->
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:28px;">
      <tr><td style="padding-bottom:12px;border-bottom:2px solid {divider_color};">
        <span style="font-size:11px;font-weight:700;letter-spacing:1.5px;
                     color:{label_color};text-transform:uppercase;font-family:Arial,sans-serif;">{_esc(section_label)}</span>
        <span style="font-size:11px;color:#aaa;margin-left:8px;">({len(items)})</span>
      </td></tr>
"""
            for s in items:
                note = _esc(s["notes"].split(".")[0] + "." if s["notes"] else "")
                headline_esc = _esc(s["headline"])
                company_esc  = _esc(s["company_name"])
                link = s.get("link", "")
                headline_html = (
                    f'<a href="{link}" style="color:{link_clr};text-decoration:none;">'
                    f'{headline_esc}</a>'
                    if link else headline_esc
                )

                html += f"""
      <tr><td style="padding:16px 0 0 0;">
        <table width="100%" cellpadding="0" cellspacing="0"
               style="border:1px solid {card_border};border-radius:6px;overflow:hidden;">
          <tr><td style="background:{card_head_bg};padding:10px 16px;
                         border-bottom:1px solid {card_border};">
            {_badge(ttype)}
            <span style="font-size:13px;font-weight:700;color:{headline_clr};
                         margin-left:10px;vertical-align:middle;font-family:Arial,sans-serif;">{company_esc}</span>
            <span style="font-size:11px;color:#aaa;float:right;
                         line-height:22px;font-family:Arial,sans-serif;">{_esc(s['date_str'])}</span>
          </td></tr>
          <tr><td style="padding:12px 16px 6px 16px;">
            <div style="font-size:14px;font-weight:600;line-height:1.4;color:{headline_clr};font-family:Arial,sans-serif;">
              {headline_html}
            </div>
          </td></tr>
"""
                if note:
                    html += f"""
          <tr><td style="padding:0 16px 14px 16px;">
            <div style="font-size:13px;color:{body_text};line-height:1.5;font-family:Arial,sans-serif;">{note}</div>
          </td></tr>
"""
                html += "        </table>\n      </td></tr>\n"

            html += "    </table>\n"

    html += f"""
    <hr style="border:none;border-top:1px solid {card_border};margin:8px 0 16px 0;">
    <p style="font-size:11px;color:{footer_clr};margin:0;font-family:Arial,sans-serif;">
      Powered by ShelleOS &middot; Google News RSS &middot; Claude Haiku
      &nbsp;&middot;&nbsp; {date_fmt}
    </p>

  </td></tr>
</table>
</td></tr>
</table>
</body></html>"""

    return html


# ─── Email send ────────────────────────────────────────────────────────────────
def send_brief(brief_html, recipient, dry_run=False):
    to_addr = GMAIL_FROM if dry_run else recipient
    tag     = " [TEST]" if dry_run else ""
    subject = f"Account Intelligence Brief — {TODAY}{tag}"

    msg = MIMEMultipart("alternative")
    msg["From"]    = GMAIL_FROM
    msg["To"]      = to_addr
    msg["Subject"] = subject
    msg.attach(MIMEText(brief_html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_FROM, GMAIL_APP_PASSWORD)
        server.send_message(msg)
    print(f"✅ Brief sent to {to_addr}")


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Account News Monitor")
    parser.add_argument("--accounts-file", required=True,
                        help="Path to .txt file with one company name per line")
    parser.add_argument("--recipient", required=True,
                        help="Email address to send the brief to (live mode)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Send test email to GMAIL_FROM (Shelle) instead of recipient")
    parser.add_argument("--datasite-brand", action="store_true",
                        help="Use Datasite brand colors (orange/gray) instead of default dark theme")
    args = parser.parse_args()

    companies = load_companies(args.accounts_file)
    num_accounts = len(companies)

    mode = f"TEST → {GMAIL_FROM}" if args.dry_run else f"LIVE → {args.recipient}"
    print(f"\nAccount News Monitor — {TODAY} [{mode}]")
    print(f"{num_accounts} companies loaded")
    print("=" * 60)

    company_names_set = {c.lower() for c in companies}
    collector = SignalCollector(company_names_set)

    # ── PASS 1: All companies in batches of 6 ────────────────────────────────
    print(f"\nPASS 1: Scanning {num_accounts} companies in batches of 6...")
    batch_size = 6
    for i in range(0, len(companies), batch_size):
        batch     = companies[i:i + batch_size]
        batch_str = " OR ".join(f'"{c}"' for c in batch)
        query     = (f"({batch_str}) acquisition OR merger OR funding OR IPO "
                     f"OR CEO OR leadership OR layoff OR partnership OR \"strategic review\"")
        results = google_news_search_signals(query, lookback_days=3)
        for s in results:
            e = collector.add(s)
            if e:
                print(f"  ✅ {e['company_name']} — {e['headline'][:70]}")
        time.sleep(0.3)

    # ── PASS 2: 72-hour breaking news check on first 40 companies ────────────
    large_caps = companies[:40]
    print(f"\nPASS 2: 72-hour breaking news check — top {len(large_caps)} companies...")
    for company in large_caps:
        results = google_news_search_signals(
            f'"{company}" CEO OR CFO OR acquisition OR merger OR layoff OR "strategic review"',
            lookback_days=3,
        )
        for s in results:
            if not s.get("company_name"):
                s["company_name"] = company
            e = collector.add(s)
            if e:
                print(f"  ✅ {e['company_name']} — {e['headline'][:70]}")
        time.sleep(0.3)

    total = len(collector.signals)
    print(f"\n{'='*60}")
    print(f"Signals found: {total}")

    brief = format_brief_html(collector.signals, num_accounts=num_accounts, dry_run=args.dry_run, datasite_brand=args.datasite_brand)

    if args.dry_run:
        print("\n" + "─"*60)
        print(f"[HTML email — {len(brief)} chars, {total} signals]")
        print("─"*60)

    send_brief(brief, recipient=args.recipient, dry_run=args.dry_run)
    print(f"Done. {total} signals | {'Test email → Shelle' if args.dry_run else f'Brief → {args.recipient}'}")


if __name__ == "__main__":
    main()
