#!/usr/bin/env python3
"""
ShelleOS Morning Signal Scanner
Searches for deal signals across Shelle's 210-company Bay Area territory,
writes triggers to Airtable, and sends a morning brief to shelle.martin@datasite.com.

Uses Google News RSS (free) + Claude Haiku (cheap) instead of Perplexity.
Perplexity is reserved for contact_hygiene.py which needs LinkedIn depth.

Usage:
  python3 morning_signal_scanner.py           # live run
  python3 morning_signal_scanner.py --dry-run # print signals, no Airtable writes or email

Setup:
  pip install requests python-dotenv anthropic
  .env requires: AIRTABLE_API_KEY, ANTHROPIC_API_KEY, GMAIL_APP_PASSWORD, GMAIL_FROM, GMAIL_TO
"""

import os
import sys
import json
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

AIRTABLE_API_KEY   = os.getenv("AIRTABLE_API_KEY")
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
GMAIL_FROM         = os.getenv("GMAIL_FROM", "shelle.k.martin@gmail.com")
GMAIL_TO           = os.getenv("GMAIL_TO", "shelle.martin@datasite.com")

missing = [k for k, v in {
    "AIRTABLE_API_KEY": AIRTABLE_API_KEY,
    "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
    "GMAIL_APP_PASSWORD": GMAIL_APP_PASSWORD,
}.items() if not v]
if missing:
    print(f"\nERROR: Missing .env keys: {', '.join(missing)}")
    sys.exit(1)

claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ─── Airtable config ──────────────────────────────────────────────────────────
BASE_ID          = "app9VxYkYesBpA7Fe"
COMPANIES_TABLE  = "tblBImf6yfRbzSB4e"
TRIGGERS_TABLE   = "tblWehiTOEQf5EnHt"

AIRTABLE_HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_API_KEY}",
    "Content-Type": "application/json",
}

# Trigger field IDs
F_HEADLINE     = "fldBWt79YNmWeSwWC"
F_TRIGGER_DATE = "fldHKSiMQEerSicK3"
F_TRIGGER_TYPE = "fldtii28utj7d7Slu"
F_STATUS       = "fldYfRlEyFnW6vi3D"
F_ACTION_TAKEN = "fldwUzPnTGaPxpKix"
F_NOTES        = "fld8597gsqRauloRN"
F_COMPANY_LINK = "fldewgxtimk3lORVC"

# Exact Airtable trigger_type values (typos and trailing spaces are intentional)
TYPE_ACQUISITION = "Acquision"
TYPE_LEADERSHIP  = "Leadership Change"
TYPE_FUNDING     = "Funding "
TYPE_SELL_SIDE   = "Sell Side "
TYPE_ACTIVIST    = "Activist Activity"
TYPE_EARNINGS    = "Earnings "
TYPE_NEWS        = "News"

TODAY = date.today().isoformat()

# ─── Company lists ────────────────────────────────────────────────────────────
TIER1 = [
    "Nvidia", "Apple", "Visa", "Netflix", "AMD", "Lam Research", "Intel",
    "Gilead", "Salesforce", "Seagate", "Equinix", "Synopsys", "Robinhood",
    "Ross Stores", "EA", "Roblox", "Workday", "PayPal", "PG&E", "Block",
    "Veeva", "NetApp", "Cooper Companies", "Clorox", "Franklin Resources",
    "TD Synnex", "Gap", "RH", "Robert Half", "TriNet", "Concentrix",
    "Central Garden & Pet", "Grocery Outlet", "Atlassian",
]

TIER2 = [
    "ACM Research", "Aehr Test", "Amprius", "AXT", "Benitec", "Bio-Rad",
    "Corsair Gaming", "C3.ai", "Coupa", "Cytek", "Dynavax", "Enovix",
    "Enphase", "Geron", "Grid Dynamics", "Guidewire", "Hercules Capital",
    "Ichor", "Innoviva", "Interlink", "Iovance", "Lucid", "McGrath Rental",
    "Mirum Pharma", "Model N", "NMI Holdings", "Pacific Biosciences",
    "Personalis", "Pony.ai", "PowerSchool", "PROCEPT BioRobotics",
    "Protagonist", "PubMatic", "Pulse Biosciences", "QuinStreet",
    "Revolution Medicines", "Rigetti", "Roadzen", "Serve Robotics",
    "Sight Sciences", "Simpson Mfg", "Soleno", "Summit Therapeutics",
    "ThredUp", "TriCo Bancshares", "Ultra Clean", "Upstart", "Vaxcyte",
    "Velo3D", "Zuora", "Kyverna", "Arcus Bio", "Armanino Foods",
    "AssetMark", "BioAge Labs",
]

PRIVATE = [
    "Agiloft", "Alation", "Allworth Financial", "Alpha Aesthetics Partners",
    "Alpine SG", "Altos Labs", "Amyris", "Applied StemCell", "Archerhall",
    "ARK Diagnostics", "Armanino", "BigPanda", "BizLink Group", "Boyd",
    "Bridgepointe Technologies", "Brightline", "Buyerlink", "Catellus",
    "Cleanwater1", "Color", "Databricks", "Domestika",
    "Educational Media Foundation", "Eikon Therapeutics", "Everlaw",
    "Exabeam", "Exadel", "FalconX", "Five Star Bank", "Fivetran",
    "Flynn Group", "FormFactor", "Freshworks", "Frontier Dental Laboratories",
    "Genesys", "GRAIL", "Gruve", "Hadron Energy", "Harborside",
    "Heffernan Insurance Brokers", "Infinium Holdings", "Inszone Insurance Services",
    "Ivalua", "IXL Learning", "Jitterbit", "Kepler Computing", "Kiteworks",
    "KoBold Metals", "LaunchDarkly", "Leia", "Life360", "Liftoff Mobile",
    "Lyra Health", "MapLight", "MariaDB", "MARS Energy Group",
    "Material Security", "Medallia", "MinIO", "MycoWorks", "Neo4j",
    "NextPower", "Nutcracker Therapeutics", "Observe", "Observe.AI",
    "OpenAI", "Paradigm Outcomes", "Philz Coffee", "PingPong",
    "Point Quest Group", "Quick Quack Car Wash Holdings", "Quicken",
    "Qwilt", "Radionetics Oncology", "RefleXion", "Reltio",
    "Riverbed Technology", "Roofstock", "Sciens Building Solutions",
    "Simpson Strong-Tie Company", "SnapLogic", "SonicWall",
    "Specialized Packaging Group", "SS8 Networks", "Standish Management",
    "Straine Dental Management", "Sumo Logic", "SurveyMonkey", "Swiftly",
    "Synthego", "Synthetik", "Synthekine", "Tarana", "The Arcticom Group",
    "Topia", "Trans Bay Cable", "TrustArc", "Turnitin", "Ultima Genomics",
    "United Business Bank", "UPSIDE Foods", "Verkada", "Vitesse Systems",
    "Weee!", "WorkBoard", "Worldpac", "Zum", "ESM Group International", "Accela",
]

# Thematic and capital markets searches (run every day)
THEMATIC_SEARCHES = [
    "Bay Area M&A deals this week",
    "Silicon Valley acquisitions last 14 days",
    "SF biotech funding 2026",
    "Bay Area tech partnerships this week",
    "California insurance acquisitions 2026",
    "enterprise software M&A last 14 days",
    "Bay Area IPO filings 2026",
    "Silicon Valley funding rounds this week",
    "Bay Area company debt financing 2026",
    "tech company IPO 2026",
]

# Tier 2 sector batch search terms (used in Google News queries)
TIER2_SECTOR_SEARCHES = [
    "Bay Area biotech acquisition funding leadership",
    "SF semiconductor acquisition funding news",
    "Bay Area fintech acquisition funding news",
    "Bay Area SaaS software acquisition merger funding",
    "Bay Area clean energy acquisition funding partnership",
]

# ─── Signal ranking ────────────────────────────────────────────────────────────
def rank_signal(trigger_type, headline, notes):
    h = (headline + " " + (notes or "")).lower()
    billions = any(x in h for x in [
        "billion", " $1b", " $2b", " $3b", " $5b", " $7b", " $10b",
        "1b deal", "2b deal", "3b deal",
    ])
    if trigger_type == TYPE_ACQUISITION and billions:
        return 1
    if trigger_type in (TYPE_ACQUISITION, TYPE_SELL_SIDE):
        return 2
    if trigger_type == TYPE_FUNDING:
        return 3
    if trigger_type == TYPE_LEADERSHIP:
        return 4
    if trigger_type == TYPE_NEWS:
        return 5
    if trigger_type == TYPE_EARNINGS:
        return 6
    return 7

# ─── Signal classification ────────────────────────────────────────────────────
def classify_signal(signal_type_str):
    s = (signal_type_str or "").lower().strip()
    if any(x in s for x in ["acqui", "merger", "divest", "buyout", "pe invest", "spac"]):
        return TYPE_ACQUISITION
    if any(x in s for x in ["fund", "ipo", "round", "capital raise", "debt", "series", "credit"]):
        return TYPE_FUNDING
    if any(x in s for x in ["leadership", "ceo", "cfo", "clo", "cbo", "coo", "chief",
                              "president", "appoint", "hire", "depart", "resign", "board"]):
        return TYPE_LEADERSHIP
    if any(x in s for x in ["sell_side", "sell side", "strategic review",
                              "exploring sale", "sale process"]):
        return TYPE_SELL_SIDE
    if any(x in s for x in ["earn", "quarterly", "revenue", "q1", "q2", "q3", "q4",
                              "fiscal", "guidance", "results", "restructur"]):
        return TYPE_EARNINGS
    if any(x in s for x in ["activist", "proxy", "shareholder"]):
        return TYPE_ACTIVIST
    return TYPE_NEWS

# ─── Airtable helpers ─────────────────────────────────────────────────────────
def get_airtable_companies():
    """Returns {name: record_id} and {lowercase_name: record_id}."""
    url = f"https://api.airtable.com/v0/{BASE_ID}/{COMPANIES_TABLE}"
    params = {"fields[]": "Company Name", "pageSize": 100}
    company_map = {}
    offset = None
    while True:
        if offset:
            params["offset"] = offset
        resp = requests.get(url, headers=AIRTABLE_HEADERS, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        for rec in data.get("records", []):
            name = rec.get("fields", {}).get("Company Name", "").strip()
            if name:
                company_map[name] = rec["id"]
                company_map[name.lower()] = rec["id"]
        offset = data.get("offset")
        if not offset:
            break
        time.sleep(0.2)
    return company_map


def get_recent_triggers(days=14):
    """Returns {company_record_id: set(trigger_types)} for last N days."""
    url = f"https://api.airtable.com/v0/{BASE_ID}/{TRIGGERS_TABLE}"
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    params = {
        "filterByFormula": f"AND({{trigger_date}} >= '{cutoff}')",
        "fields[]": [F_TRIGGER_TYPE, F_COMPANY_LINK, F_HEADLINE],
        "pageSize": 100,
    }
    trigger_map = {}
    headline_map = {}
    offset = None
    while True:
        if offset:
            params["offset"] = offset
        resp = requests.get(url, headers=AIRTABLE_HEADERS, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        for rec in data.get("records", []):
            f = rec.get("fields", {})
            ttype    = f.get(F_TRIGGER_TYPE, "")
            headline = f.get(F_HEADLINE, "").lower()
            links    = f.get(F_COMPANY_LINK, [])
            for cid in links:
                trigger_map.setdefault(cid, set()).add(ttype)
                headline_map.setdefault(cid, set()).add(headline[:60])
        offset = data.get("offset")
        if not offset:
            break
        time.sleep(0.2)
    return trigger_map, headline_map


def write_trigger(company_record_id, headline, notes, trigger_type, trigger_date, dry_run=False):
    if dry_run:
        return True
    fields = {
        F_HEADLINE:     headline[:255],
        F_TRIGGER_DATE: trigger_date,
        F_TRIGGER_TYPE: trigger_type,
        F_STATUS:       "Todo",
        F_ACTION_TAKEN: False,
        F_NOTES:        notes or "",
    }
    if company_record_id:
        fields[F_COMPANY_LINK] = [company_record_id]
    url = f"https://api.airtable.com/v0/{BASE_ID}/{TRIGGERS_TABLE}"
    resp = requests.post(url, headers=AIRTABLE_HEADERS, json={"fields": fields}, timeout=20)
    if not resp.ok:
        print(f"    ERROR writing trigger: {resp.status_code} {resp.text[:150]}")
        return False
    return True

# ─── Google News RSS + Claude Haiku ──────────────────────────────────────────

def fetch_google_news(query, lookback_days=14):
    """
    Fetch Google News RSS for a query. Returns list of recent articles within lookback window.
    Each article: {title, snippet, date_str, link}
    """
    encoded = quote_plus(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    try:
        resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception:
        return []

    cutoff = datetime.now() - timedelta(days=lookback_days)
    articles = []

    for item in root.findall(".//item")[:12]:
        title       = item.findtext("title") or ""
        description = item.findtext("description") or ""
        pub_date_str = item.findtext("pubDate") or ""
        link        = item.findtext("link") or ""

        # Parse publication date
        try:
            pub_date = parsedate_to_datetime(pub_date_str).replace(tzinfo=None)
            if pub_date < cutoff:
                continue
            date_str = pub_date.strftime("%Y-%m-%d")
        except Exception:
            date_str = TODAY

        # Strip HTML tags from description
        import re
        snippet = re.sub(r"<[^>]+>", "", description)[:300]

        articles.append({
            "title": title,
            "snippet": snippet,
            "date_str": date_str,
            "link": link,
        })

    return articles


def classify_articles_with_haiku(articles, context_query):
    """
    Use Claude Haiku to extract deal signals from a list of news article titles/snippets.
    Returns list of {company_name, headline, signal_type, notes, date_str} dicts.
    """
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
- Leadership changes at C-suite or Corp Dev/GC level
- Earnings with M&A or strategic commentary
- Strategic review / exploring sale
- Significant partnerships (JVs, distribution deals, platform integrations)
- Layoffs or major expansions

Skip: press releases, product minor updates, awards, analyst ratings, stock price moves.
Max 3 signals. If nothing real, return [].

JSON format only, no markdown:
[
  {{
    "company_name": "exact company name",
    "headline": "one-line summary under 100 chars",
    "signal_type": "acquisition|funding|leadership|earnings|sell_side|partnership|news|layoffs",
    "notes": "2-3 sentences: what happened and why it matters for M&A outreach",
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


def google_news_search_signals(query, lookback_days=14):
    """
    Main search function. Fetches Google News RSS, classifies with Haiku.
    Returns same format as the old perplexity_search_signals().
    """
    articles = fetch_google_news(query, lookback_days=lookback_days)
    if not articles:
        return []
    return classify_articles_with_haiku(articles, query)

# ─── Private company rotation ─────────────────────────────────────────────────
def get_private_companies_for_today():
    """Rotate through private companies — 35 per day based on day of year."""
    day_of_year = datetime.now().timetuple().tm_yday
    n = 35
    total = len(PRIVATE)
    start = (day_of_year * n) % total
    indices = [(start + i) % total for i in range(n)]
    return [PRIVATE[i] for i in indices]

# ─── Signal collection + dedup ────────────────────────────────────────────────
class SignalCollector:
    def __init__(self, company_map, trigger_map, headline_map):
        self.company_map          = company_map
        self.trigger_map          = trigger_map
        self.headline_map         = headline_map
        self.signals              = []
        self.written_this_run     = {}   # {company_id: set(trigger_types)}
        self.company_signal_counts = {}  # {company_id: int} — max 2 per company per day

    def _find_company_id(self, company_name):
        return (self.company_map.get(company_name) or
                self.company_map.get(company_name.lower()))

    def _is_dupe(self, company_id, trigger_type, headline):
        if company_id and trigger_type in self.trigger_map.get(company_id, set()):
            return True, "existing_airtable"
        if company_id:
            existing_headlines = self.headline_map.get(company_id, set())
            h_short = headline.lower()[:60]
            if any(h_short[:30] in eh for eh in existing_headlines):
                return True, "similar_headline"
        if company_id and trigger_type in self.written_this_run.get(company_id, set()):
            return True, "same_run"
        return False, None

    def add(self, signal, is_catchup=False, require_territory=False):
        company_name = signal.get("company_name", "").strip()
        headline     = signal.get("headline", "").strip()
        notes        = signal.get("notes", "").strip()
        signal_type  = signal.get("signal_type", "news")
        date_str     = signal.get("date_str", TODAY)
        trigger_type = classify_signal(signal_type)

        if not headline or not company_name:
            return None

        # For thematic searches, only keep territory companies
        if require_territory:
            all_territory = set(c.lower() for c in TIER1 + TIER2 + PRIVATE)
            cname_lower = company_name.lower()
            in_territory = (
                cname_lower in all_territory or
                self._find_company_id(company_name) is not None
            )
            if not in_territory:
                return None

        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            date_str = TODAY

        company_id = self._find_company_id(company_name)

        is_dup, _ = self._is_dupe(company_id, trigger_type, headline)
        if is_dup:
            return None

        count = self.company_signal_counts.get(company_id or company_name, 0)
        if count >= 2:
            return None

        if is_catchup:
            headline = f"CATCH-UP: {headline}"

        rank = rank_signal(trigger_type, headline, notes)

        entry = {
            "company_name": company_name,
            "company_id":   company_id,
            "headline":     headline,
            "notes":        notes,
            "trigger_type": trigger_type,
            "date_str":     date_str,
            "rank":         rank,
            "is_catchup":   is_catchup,
        }
        self.signals.append(entry)

        if company_id:
            self.written_this_run.setdefault(company_id, set()).add(trigger_type)
            self.trigger_map.setdefault(company_id, set()).add(trigger_type)
        self.company_signal_counts[company_id or company_name] = count + 1

        return entry

    def write_all_to_airtable(self, dry_run=False):
        written = 0
        for s in self.signals:
            ok = write_trigger(
                s["company_id"], s["headline"], s["notes"],
                s["trigger_type"], s["date_str"], dry_run=dry_run,
            )
            if ok:
                written += 1
        return written

# ─── Morning brief formatter ──────────────────────────────────────────────────
def format_morning_brief(collector, written_count, dry_run=False):
    signals = sorted(collector.signals, key=lambda s: s["rank"])
    hot     = [s for s in signals if not s["is_catchup"] and s["rank"] <= 4][:5]
    catchup = [s for s in signals if s["is_catchup"]]

    lines = [f"TERRITORY MORNING BRIEF — {TODAY}"]
    if dry_run:
        lines[0] += " [DRY RUN]"
    lines.append("")

    lines.append("HOT SIGNALS")
    if hot:
        for s in hot:
            lines.append(f"  • {s['company_name']}: {s['headline']}")
            if s["notes"]:
                lines.append(f"    {s['notes'][:120]}")
    else:
        lines.append("  Quiet morning — no hot signals.")
    lines.append("")

    if catchup:
        lines.append("CATCH-UP SIGNALS (from 14-day lookback)")
        for s in catchup:
            lines.append(f"  • {s['company_name']}: {s['headline']} [{s['date_str']}]")
        lines.append("")

    by_type = {}
    for s in signals:
        by_type.setdefault(s["trigger_type"].strip(), []).append(s)

    type_labels = {
        "Acquision": "M&A", "Sell Side": "Sell Side", "Funding": "Funding",
        "Leadership Change": "Leadership", "Earnings": "Earnings",
        "News": "Partnerships & News", "Activist Activity": "Activist",
    }
    lines.append("NEWS ROUNDUP")
    for ttype, label in type_labels.items():
        items = by_type.get(ttype, []) + by_type.get(ttype + " ", [])
        if items:
            lines.append(f"  {label}:")
            for s in items:
                lines.append(f"    • {s['company_name']}: {s['headline']}")
    lines.append("")

    lines.append("OUTREACH ANGLES")
    top3 = [s for s in signals if s["rank"] <= 3][:3]
    for s in top3:
        lines.append(f"  • {s['company_name']} ({s['trigger_type'].strip()}): {s['notes'][:100] if s['notes'] else s['headline']}")
    if not top3:
        lines.append("  No high-priority outreach angles today.")
    lines.append("")

    lines.append("AIRTABLE UPDATE")
    mode = "DRY RUN — no writes" if dry_run else f"{written_count} trigger(s) written"
    lines.append(f"  {mode}")
    for s in signals:
        linked = f"[linked: {s['company_id']}]" if s["company_id"] else "[NO Airtable match]"
        lines.append(f"  • {s['company_name']}: {s['headline'][:60]} ({s['trigger_type'].strip()}) {linked}")

    return "\n".join(lines)

# ─── Send morning brief via Gmail ─────────────────────────────────────────────
def send_morning_brief(brief_text, dry_run=False):
    subject = f"ShelleOS Morning Brief — {TODAY}"
    if dry_run:
        print(f"\n{'='*60}")
        print(f"[DRY RUN] Would send to: {GMAIL_TO}")
        print(f"Subject: {subject}")
        print(brief_text)
        return
    msg = MIMEMultipart()
    msg["From"]    = GMAIL_FROM
    msg["To"]      = GMAIL_TO
    msg["Subject"] = subject
    msg.attach(MIMEText(brief_text, "plain"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_FROM, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        print(f"\n✅ Morning brief sent to {GMAIL_TO}")
    except Exception as e:
        print(f"\nERROR sending brief: {e}")

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="ShelleOS Morning Signal Scanner")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print signals without writing to Airtable or sending email")
    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    print(f"\nShelleOS Morning Signal Scanner — {TODAY} [{mode}]")
    print("=" * 60)

    print("Loading Airtable company map...")
    company_map = get_airtable_companies()
    print(f"  {len(company_map)//2} companies in Airtable")

    print("Loading existing triggers (last 14 days)...")
    trigger_map, headline_map = get_recent_triggers(days=14)
    print(f"  {sum(len(v) for v in trigger_map.values())} existing trigger types loaded\n")

    collector = SignalCollector(company_map, trigger_map, headline_map)

    # ── PASS 1: Tier 1 individual searches ────────────────────────────────────
    print("PASS 1: Tier 1 — individual searches (48h for leadership/earnings, 14d for M&A/funding)")
    for company in TIER1:
        # Short lookback — leadership, earnings, layoffs
        results = google_news_search_signals(
            f'"{company}" leadership OR earnings OR layoffs OR executive OR CEO OR CFO OR "general counsel"',
            lookback_days=2,
        )
        for s in results:
            if not s.get("company_name"):
                s["company_name"] = company
            e = collector.add(s)
            if e:
                print(f"  ✅ {e['company_name']} — {e['headline'][:70]}")
        time.sleep(0.3)

        # Long lookback — M&A, funding, partnerships
        results = google_news_search_signals(
            f'"{company}" acquisition OR merger OR funding OR IPO OR partnership OR "strategic review" OR "sell side"',
            lookback_days=14,
        )
        for s in results:
            if not s.get("company_name"):
                s["company_name"] = company
            e = collector.add(s)
            if e:
                print(f"  ✅ {e['company_name']} — {e['headline'][:70]}")
        time.sleep(0.3)

    # ── PASS 2: Tier 2 sector batch searches ──────────────────────────────────
    print(f"\nPASS 2: Tier 2 sector searches")
    for query in TIER2_SECTOR_SEARCHES:
        results = google_news_search_signals(query, lookback_days=7)
        for s in results:
            e = collector.add(s)
            if e:
                print(f"  ✅ {e['company_name']} — {e['headline'][:70]}")
        time.sleep(0.3)

    # Tier 2 individual searches in batches of 6
    print(f"  Individual Tier 2 searches (batches of 6)")
    batch_size = 6
    for i in range(0, len(TIER2), batch_size):
        batch = TIER2[i:i + batch_size]
        batch_str = " OR ".join(f'"{c}"' for c in batch)
        query = f"({batch_str}) acquisition OR merger OR funding OR leadership OR partnership"
        results = google_news_search_signals(query, lookback_days=7)
        for s in results:
            e = collector.add(s)
            if e:
                print(f"  ✅ {e['company_name']} — {e['headline'][:70]}")
        time.sleep(0.3)

    # ── PASS 3: Private company searches (rotated 35/day) ─────────────────────
    print(f"\nPASS 3: Private companies (35 today, rotated daily)")
    todays_private = get_private_companies_for_today()
    batch_size = 5
    for i in range(0, len(todays_private), batch_size):
        batch = todays_private[i:i + batch_size]
        batch_str = " OR ".join(f'"{c}"' for c in batch)
        query = f"({batch_str}) acquisition OR merger OR funding OR leadership OR partnership OR news"
        results = google_news_search_signals(query, lookback_days=14)
        for s in results:
            e = collector.add(s)
            if e:
                print(f"  ✅ {e['company_name']} — {e['headline'][:70]}")
        time.sleep(0.3)

    # ── PASS 4: Thematic + capital markets searches ───────────────────────────
    print(f"\nPASS 4: Thematic and capital markets searches")
    for query in THEMATIC_SEARCHES:
        results = google_news_search_signals(query, lookback_days=14)
        for s in results:
            e = collector.add(s, require_territory=True)
            if e:
                print(f"  ✅ {e['company_name']} — {e['headline'][:70]}")
        time.sleep(0.3)

    # ── PASS 5: Tier 1 Sanity Check (MANDATORY) ───────────────────────────────
    print(f"\nPASS 5: Tier 1 sanity check — 14-day M&A/funding/IPO/partnership pass")
    for company in TIER1:
        query = f'"{company}" acquisition OR merger OR funding OR IPO OR partnership'
        results = google_news_search_signals(query, lookback_days=14)
        for s in results:
            if not s.get("company_name"):
                s["company_name"] = company
            s["is_catchup"] = True
            e = collector.add(s, is_catchup=True)
            if e:
                print(f"  🔁 CATCH-UP {e['company_name']} — {e['headline'][:70]}")
        time.sleep(0.3)

    # ── Write to Airtable ─────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    total = len(collector.signals)
    print(f"Signals collected: {total}")

    written = collector.write_all_to_airtable(dry_run=args.dry_run)
    action = "Would write" if args.dry_run else "Written"
    print(f"{action}: {written} triggers to Airtable")

    brief = format_morning_brief(collector, written, dry_run=args.dry_run)
    send_morning_brief(brief, dry_run=args.dry_run)

    print(f"\nDone. {total} signals | {written} written to Airtable | Brief sent to {GMAIL_TO}")
    if args.dry_run:
        print("DRY RUN — nothing was written.")
    print()


if __name__ == "__main__":
    main()
