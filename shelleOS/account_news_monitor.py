#!/usr/bin/env python3
"""
Account News Monitor — John Stallings Territory
Scans Google News RSS for deal signals across 238 accounts,
classifies with Claude Haiku, emails a daily brief.

NO Airtable. NO Google Sheets API. Fully standalone.

Usage:
  python3 account_news_monitor.py           # live run — sends to GMAIL_TO (John)
  python3 account_news_monitor.py --dry-run # sends to GMAIL_FROM (Shelle) for review

.env requires: ANTHROPIC_API_KEY, GMAIL_APP_PASSWORD, GMAIL_FROM, GMAIL_TO
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
GMAIL_TO           = os.getenv("GMAIL_TO", "John.stallings@datasite.com")

missing = [k for k, v in {
    "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
    "GMAIL_APP_PASSWORD": GMAIL_APP_PASSWORD,
}.items() if not v]
if missing:
    print(f"\nERROR: Missing env vars: {', '.join(missing)}")
    sys.exit(1)

claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
TODAY = date.today().isoformat()

# ─── Company list (238 accounts) ──────────────────────────────────────────────
# Source: John Stallings - Sheet1.csv
COMPANIES = [
    "UnitedHealth Group", "Cardinal Health", "General Motors", "Ford Motor",
    "Elevance Health", "Walgreens Boots Alliance", "Kroger", "Marathon Petroleum",
    "State Farm Insurance", "Humana", "Target", "Archer Daniels Midland",
    "Procter & Gamble", "Progressive", "Boeing", "Caterpillar", "Allstate",
    "Nationwide", "United Airlines Holdings", "AbbVie", "Deere", "Eli Lilly",
    "Dow", "U.S. Bancorp", "Abbott Laboratories", "Best Buy", "CHS",
    "GE Aerospace", "US Foods Holding", "Mondelez International", "Cummins",
    "Penske Automotive Group", "McDonald's", "Kraft Heinz", "3M",
    "Discover Financial Services", "Jones Lang LaSalle", "Lear", "Sherwin-Williams",
    "Exelon", "Stryker", "Reinsurance Group of America", "CDW", "Parker-Hannifin",
    "General Mills", "American Electric Power", "GE HealthCare Technologies",
    "Cleveland-Cliffs", "Goodyear Tire & Rubber", "Ameriprise Financial",
    "C.H. Robinson Worldwide", "Steel Dynamics", "Emerson Electric", "W.W. Grainger",
    "Corteva", "O'Reilly Automotive", "Whirlpool", "Ally Financial", "Edward Jones",
    "Land O'Lakes", "Principal Financial", "Illinois Tool Works", "Northern Trust",
    "Auto-Owners Insurance", "Ecolab", "Baxter International", "Casey's General Stores",
    "LKQ", "BorgWarner", "Western & Southern Financial Group", "Xcel Energy",
    "Fifth Third Bancorp", "FirstEnergy", "Kellanova", "DTE Energy",
    "Berry Global Group", "Conagra Brands", "Huntington Bancshares", "Hormel Foods",
    "Graybar Electric", "Molson Coors Beverage", "Arthur J. Gallagher",
    "Cincinnati Financial", "Ulta Beauty", "BrightSpring Health Services",
    "Andersons", "Owens Corning", "Thrivent Financial for Lutherans",
    "Motorola Solutions", "Autoliv", "Dana", "Thor Industries", "Cintas",
    "SpartanNash", "Ace Hardware", "KeyCorp", "Seaboard", "Dover",
    "Packaging Corp. of America", "American Financial Group", "Solventum",
    "Old Republic International", "Securian Financial Group", "J.M. Smucker",
    "Vertiv Holdings", "Welltower", "TransDigm Group", "Post Holdings", "Masco",
    "Zimmer Biomet Holdings", "Yum Brands", "Fastenal", "CMS Energy", "Core & Main",
    "Ingredion", "RPM International", "Ameren", "Bath & Body Works", "Polaris",
    "APi Group", "UFP Industries", "Hyatt Hotels", "Patterson", "Olin", "O-I Glass",
    "Spirit AeroSystems Holdings", "Victoria's Secret", "CME Group",
    "American Axle & Manufacturing", "Camping World Holdings", "Simon Property Group",
    "Stifel Financial", "CF Industries Holdings", "Evergy", "OneMain Holdings",
    "NiSource", "Greif", "Rocket Companies", "Texas Roadhouse", "Lineage",
    "Brunswick", "Option Care Health", "Zebra Technologies",
    "Telephone & Data Systems", "Country Financial", "Abercrombie & Fitch",
    "Somnigroup International", "Ventas", "Bread Financial Holdings",
    "Domino's Pizza", "Kemper", "AMC Entertainment Holdings",
    "Fortune Brands Innovations", "Ryerson Holding", "Toro", "Timken", "M/I Homes",
    "Applied Industrial Technologies", "CNO Financial Group", "Elanco Animal Health",
    "Leggett & Platt", "Kelly Services", "Medical Mutual of Ohio", "Hyster-Yale",
    "Peabody Energy", "Calumet Specialty Products Partners", "TransUnion",
    "Brown-Forman", "Cboe Global Markets", "Lincoln Electric Holdings",
    "Euronet Worldwide", "Wintrust Financial", "Hub Group", "Middleby",
    "Mettler-Toledo International", "Visteon", "Diebold Nixdorf", "LCI Industries",
    "Patrick Industries", "MillerKnoll", "H&R Block", "Donaldson", "AptarGroup",
    "H.B. Fuller", "Scotts Miracle-Gro", "Federated Mutual Insurance",
    "Garrett Motion", "Worthington Steel", "Phinia", "TreeHouse Foods",
    "Jackson Financial", "IDEX", "Avient", "Allison Transmission Holdings",
    "Sun Communities", "Atkore", "Hillenbrand", "Steelcase", "Designer Brands",
    "Equity Residential", "Winnebago Industries", "Old National Bancorp",
    "Installed Building Products", "Alight", "Cadence Bank", "Knife River",
    "Energizer Holdings", "Advanced Drainage Systems", "UL Solutions",
    "Everus Construction Group", "UMB Financial", "Vista Outdoor", "Churchill Downs",
    "Cooper-Standard Holdings", "Caleres", "WK Kellogg", "Titan Machinery",
    "MasterBrand", "Nordson", "Cargill", "Koch Industries", "Worldpay",
    "EQ Office", "Reyes Holdings", "Enterprise Mobility", "Medline Industries",
    "Meijer", "Gordon Food Service", "World Wide Technology", "Tenneco",
    "Dabico Airport Solutions", "Hy-Vee", "Univar Solutions", "OSI Group",
    "Greatest American Outdoors Group", "Avant",
]

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
    # Check if any list company name starts with or contains the returned name
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

        # Only keep companies in John's list
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
    """Minimal HTML escaping."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_brief_html(signals, dry_run=False):
    signals = sorted(signals, key=lambda s: s["rank"])
    total   = len(signals)
    tag     = " · TEST" if dry_run else ""
    date_fmt = datetime.strptime(TODAY, "%Y-%m-%d").strftime("%A, %B %-d, %Y")

    # ── Header ──────────────────────────────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f0f2f5;font-family:Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f2f5;">
<tr><td align="center" style="padding:24px 12px;">
<table width="680" cellpadding="0" cellspacing="0" style="max-width:680px;width:100%;">

  <!-- HEADER -->
  <tr><td style="background:#1a2332;border-radius:8px 8px 0 0;padding:28px 32px;">
    <div style="color:#fff;font-size:26px;font-weight:700;letter-spacing:-0.5px;">
      Account Intelligence Brief{tag}
    </div>
    <div style="color:#8899aa;font-size:14px;margin-top:6px;">
      {date_fmt} &nbsp;|&nbsp; {len(COMPANIES)} accounts monitored &nbsp;|&nbsp;
      <strong style="color:#fff;">{total} signal{'s' if total != 1 else ''} today</strong>
    </div>
  </td></tr>

  <!-- BODY -->
  <tr><td style="background:#fff;border-radius:0 0 8px 8px;padding:28px 32px;">
"""

    if not signals:
        html += (
            '<p style="color:#555;font-size:15px;">'
            'Quiet day — no significant signals found across your 238 accounts.</p>'
        )
    else:
        for ttype, section_label in SECTION_LABELS:
            items = [s for s in signals if s["trigger_type"] == ttype]
            if not items:
                continue

            color, _ = BADGE_COLORS.get(ttype, ("#7f8c8d", ttype))
            html += f"""
    <!-- SECTION: {section_label} -->
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:28px;">
      <tr><td style="padding-bottom:12px;border-bottom:2px solid {color};">
        <span style="font-size:11px;font-weight:700;letter-spacing:1.5px;
                     color:{color};text-transform:uppercase;">{_esc(section_label)}</span>
        <span style="font-size:11px;color:#aaa;margin-left:8px;">({len(items)})</span>
      </td></tr>
"""
            for s in items:
                note = _esc(s["notes"].split(".")[0] + "." if s["notes"] else "")
                headline_esc = _esc(s["headline"])
                company_esc  = _esc(s["company_name"])
                link = s.get("link", "")
                headline_html = (
                    f'<a href="{link}" style="color:#1a2332;text-decoration:none;">'
                    f'{headline_esc}</a>'
                    if link else headline_esc
                )

                html += f"""
      <tr><td style="padding:16px 0 0 0;">
        <table width="100%" cellpadding="0" cellspacing="0"
               style="border:1px solid #e8ecf0;border-radius:6px;overflow:hidden;">
          <!-- card header -->
          <tr><td style="background:#f8f9fb;padding:10px 16px;
                         border-bottom:1px solid #e8ecf0;">
            {_badge(ttype)}
            <span style="font-size:13px;font-weight:700;color:#1a2332;
                         margin-left:10px;vertical-align:middle;">{company_esc}</span>
            <span style="font-size:11px;color:#aaa;float:right;
                         line-height:22px;">{_esc(s['date_str'])}</span>
          </td></tr>
          <!-- headline -->
          <tr><td style="padding:12px 16px 6px 16px;">
            <div style="font-size:14px;font-weight:600;line-height:1.4;color:#1a2332;">
              {headline_html}
            </div>
          </td></tr>
"""
                if note:
                    html += f"""
          <!-- notes -->
          <tr><td style="padding:0 16px 14px 16px;">
            <div style="font-size:13px;color:#555;line-height:1.5;">{note}</div>
          </td></tr>
"""
                html += "        </table>\n      </td></tr>\n"

            html += "    </table>\n"

    # ── Footer ───────────────────────────────────────────────────────────────────
    html += f"""
    <hr style="border:none;border-top:1px solid #e8ecf0;margin:8px 0 16px 0;">
    <p style="font-size:11px;color:#aaa;margin:0;">
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
def send_brief(brief_html, dry_run=False):
    recipient = GMAIL_FROM if dry_run else GMAIL_TO
    tag       = " [TEST]" if dry_run else ""
    subject   = f"Account Intelligence Brief — {TODAY}{tag}"

    msg = MIMEMultipart("alternative")
    msg["From"]    = GMAIL_FROM
    msg["To"]      = recipient
    msg["Subject"] = subject
    msg.attach(MIMEText(brief_html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_FROM, GMAIL_APP_PASSWORD)
        server.send_message(msg)
    print(f"✅ Brief sent to {recipient}")


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Account News Monitor")
    parser.add_argument("--dry-run", action="store_true",
                        help="Send test email to GMAIL_FROM (Shelle) instead of GMAIL_TO (John)")
    args = parser.parse_args()

    mode = f"TEST → {GMAIL_FROM}" if args.dry_run else f"LIVE → {GMAIL_TO}"
    print(f"\nAccount News Monitor — {TODAY} [{mode}]")
    print(f"{len(COMPANIES)} companies loaded")
    print("=" * 60)

    company_names_set = {c.lower() for c in COMPANIES}
    collector = SignalCollector(company_names_set)

    # ── PASS 1: All 238 companies in batches of 6 ─────────────────────────────
    print(f"\nPASS 1: Scanning {len(COMPANIES)} companies in batches of 6...")
    batch_size = 6
    for i in range(0, len(COMPANIES), batch_size):
        batch     = COMPANIES[i:i + batch_size]
        batch_str = " OR ".join(f'"{c}"' for c in batch)
        query     = (f"({batch_str}) acquisition OR merger OR funding OR IPO "
                     f"OR CEO OR leadership OR layoff OR partnership OR \"strategic review\"")
        results = google_news_search_signals(query, lookback_days=3)
        for s in results:
            e = collector.add(s)
            if e:
                print(f"  ✅ {e['company_name']} — {e['headline'][:70]}")
        time.sleep(0.3)

    # ── PASS 2: High-signal individual searches for large-caps ────────────────
    # Run a focused 72-hour pass on the largest companies for breaking news
    large_caps = COMPANIES[:40]  # first 40 are the largest by revenue order
    print(f"\nPASS 2: 72-hour breaking news check — top 40 companies...")
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

    # ── Done ──────────────────────────────────────────────────────────────────
    total = len(collector.signals)
    print(f"\n{'='*60}")
    print(f"Signals found: {total}")

    brief = format_brief_html(collector.signals, dry_run=args.dry_run)

    if args.dry_run:
        print("\n" + "─"*60)
        print(f"[HTML email — {len(brief)} chars, {len(collector.signals)} signals]")
        print("─"*60)

    send_brief(brief, dry_run=args.dry_run)
    print(f"Done. {total} signals | {'Test email → Shelle' if args.dry_run else 'Brief → John'}")


if __name__ == "__main__":
    main()
