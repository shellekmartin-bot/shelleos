#!/usr/bin/env python3
"""
ShelleOS Contact Hygiene Scanner
Runs daily. Reviews 20 companies per run:
1. Verifies existing contacts via web search — flags changes only
2. Hunts for new ICP contacts not yet in Airtable
3. Creates placeholder records for new contacts (contact_status = Unverified)
4. Updates company last_hygine_review date
5. Sends Gmail summary to shelle.martin@datasite.com

Usage:
  python3 contact_hygiene.py           # live run
  python3 contact_hygiene.py --dry-run # print what would change, no writes

Rules (from spec — do not violate):
  - NEVER delete records
  - NEVER change no_longer_there on existing contacts
  - NEVER change contact_status on existing contacts
  - ONLY write to: contact notes (flags only), new placeholder records, company hygiene date
  - Always PREPEND to existing notes — never overwrite
  - Only flag problems — do NOT write "all clear" to Airtable
"""

import os
import sys
import json
import time
import smtplib
import argparse
import requests
from datetime import datetime, date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
import anthropic

# ─── Load credentials ─────────────────────────────────────────────────────────
load_dotenv()

AIRTABLE_API_KEY   = os.getenv("AIRTABLE_API_KEY")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
GMAIL_FROM         = os.getenv("GMAIL_FROM", "shelle.k.martin@gmail.com")
GMAIL_TO           = os.getenv("GMAIL_TO", "shelle.martin@datasite.com")

missing = [k for k, v in {
    "AIRTABLE_API_KEY": AIRTABLE_API_KEY,
    "PERPLEXITY_API_KEY": PERPLEXITY_API_KEY,
    "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
    "GMAIL_APP_PASSWORD": GMAIL_APP_PASSWORD,
}.items() if not v]

if missing:
    print(f"\nERROR: Missing .env keys: {', '.join(missing)}")
    sys.exit(1)

# ─── Airtable config ──────────────────────────────────────────────────────────
BASE_ID         = "app9VxYkYesBpA7Fe"
CONTACTS_TABLE  = "tblndO8S7Nnqmsenh"
COMPANIES_TABLE = "tblBImf6yfRbzSB4e"

AIRTABLE_HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_API_KEY}",
    "Content-Type": "application/json",
}

TODAY = date.today().isoformat()

# Contact field IDs (for writes)
CF_FULL_NAME      = "fldd9FRGp4cw6sjjl"
CF_TITLE          = "fldDLaBYjORBog0UJ"
CF_COMPANY_LINK   = "fldIu3EEUl0k3ILW5"
CF_EMAIL          = "fldaNRzmOAXZ7GVTv"
CF_NO_LONGER      = "fld7HXNKqzo5zDGgd"
CF_STATUS         = "fldnBsROlVJ2eCLOd"
CF_NOTES          = "fldUtuEVobKLgVHdj"
CF_TIER           = "fldLKyi3qDhpoQ2xR"

# Company field IDs (for writes)
CO_HYGIENE_DATE   = "fldheCpcsVp3sbnF1"

# ─── First name variations (duplicate check) ──────────────────────────────────
NAME_VARIANTS = {
    "jim": "james", "bob": "robert", "rob": "robert", "bill": "william",
    "mike": "michael", "mick": "michael", "dan": "daniel", "dave": "david",
    "chris": "christopher", "liz": "elizabeth", "beth": "elizabeth",
    "betty": "elizabeth", "tom": "thomas", "rich": "richard", "dick": "richard",
    "chuck": "charles", "charlie": "charles", "steve": "steven",
    "stephen": "steven", "matt": "matthew", "pat": "patrick",
    "nick": "nicholas", "tony": "anthony", "joe": "joseph", "sam": "samuel",
    "ben": "benjamin", "al": "albert", "ken": "kenneth", "ron": "ronald",
    "don": "donald", "andy": "andrew", "jeff": "jeffrey", "greg": "gregory",
    "jon": "jonathan", "jonathon": "jonathan", "kate": "katherine",
    "kath": "katherine", "jen": "jennifer", "jenny": "jennifer",
    "barb": "barbara", "sue": "susan", "susie": "susan", "pam": "pamela",
    "becky": "rebecca", "becca": "rebecca", "maggie": "margaret",
    "meg": "margaret", "cindy": "cynthia", "debbie": "deborah",
    "deb": "deborah", "sandy": "sandra", "terri": "theresa", "tina": "christina",
}

def normalize_first_name(name):
    n = name.lower().strip()
    return NAME_VARIANTS.get(n, n)

# ─── Tier auto-assignment ──────────────────────────────────────────────────────
T1_KEYWORDS = [
    "corp dev", "corporate dev", "corporate development", "corpdev",
    "m&a", "m & a", "mergers", "mergers and acquisitions", "merger",
    "acquisition", "acquisitions", "transactional", "transactions",
    "head of m&a", "vp m&a", "vp, m&a", "director of m&a",
    "integration", "post-merger", "head of integration", "integration lead",
    "integration director", "counsel", "general counsel", "gc", "chief legal",
    "clo", "head of legal", "vp legal", "vp, legal", "vice president legal",
    "deputy general counsel", "deputy gc", "associate general counsel", "associate gc",
    "assistant general counsel", "assistant gc", "senior counsel",
    "m&a counsel", "antitrust counsel", "securities counsel", "transactions counsel",
    "corporate counsel", "deal counsel", "ip counsel", "investment", "portfolio",
    "venture", "business dev", "new ventures", "strat finance", "chief of staff",
    "investor relations", "corporate strategy", "strategic planning",
    "strategic partnerships", "chief business officer", "cbo", "licensing",
    "business development", "alliance", "alliances", "alliance management",
    "alliance manager", "head of alliances", "strategic alliances",
    "partnerships", "out-license", "in-license", "outlicense", "inlicense",
    "collaboration", "collaborations",
]
T2_KEYWORDS = [
    "cfo", "chief financial", "ceo", "chief executive", "coo",
    "president", "founder", "vp finance", "controller", "treasurer",
    "vp strategy", "chief strategy", "executive director", "chairman",
]

def assign_tier(title):
    t = title.lower()
    if any(k in t for k in T1_KEYWORDS):
        return "T1"
    if any(k in t for k in T2_KEYWORDS):
        return "T2"
    return "T3"

# ─── Company domain heuristic ─────────────────────────────────────────────────
def guess_domain(company_name):
    """Best-effort domain guess. Always flag for verification."""
    import re
    name = company_name.lower()
    name = re.sub(r"\b(inc|llc|corp|ltd|co|the|group|holdings|company|technologies|solutions|services|systems)\b", "", name)
    name = re.sub(r"[^a-z0-9]", "", name)
    return f"{name}.com" if name else ""

# ─── Airtable helpers ─────────────────────────────────────────────────────────

def airtable_get_all(table_id, params):
    """Paginate through all Airtable records for a query."""
    url = f"https://api.airtable.com/v0/{BASE_ID}/{table_id}"
    records = []
    offset = None
    while True:
        if offset:
            params["offset"] = offset
        resp = requests.get(url, headers=AIRTABLE_HEADERS, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        records.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            break
        time.sleep(0.2)
    return records


def get_companies_to_review(n=20):
    """Fetch companies sorted by last_hygine_review — nulls first, then oldest."""
    records = airtable_get_all(COMPANIES_TABLE, {
        "fields[]": ["Company Name", "last_hygine_review", "is_strategic", "bucket_acquirer", "Contacts"],
        "pageSize": 100,
    })
    def sort_key(rec):
        d = rec.get("fields", {}).get("last_hygine_review", "")
        return d if d else "0000-00-00"
    records.sort(key=sort_key)
    return records[:n]


def get_contacts_for_company(company_record, active_only=True):
    """
    Fetch contacts linked to this company.
    active_only=True  → returns Active contacts only (for verification)
    active_only=False → returns ALL contacts regardless of status (for dedup)
    """
    contact_ids = company_record.get("fields", {}).get("Contacts", [])
    if not contact_ids:
        return []

    contact_ids = contact_ids[:50]
    formula_parts = [f"RECORD_ID() = '{cid}'" for cid in contact_ids]
    formula = f"OR({', '.join(formula_parts)})"

    records = airtable_get_all(CONTACTS_TABLE, {
        "filterByFormula": formula,
        "pageSize": 50,
    })

    if not active_only:
        return records  # All contacts regardless of status or no_longer_there — used for duplicate check

    # Active only — filter out no_longer_there, Do not call, Dormant, Unverified
    active = []
    for rec in records:
        f = rec.get("fields", {})
        if f.get("no_longer_there"):
            continue
        status = (f.get("contact_status") or "").strip()
        if status in ("Do not call", "Do not call ", "Dormant", "Unverified"):
            continue
        active.append(rec)
    return active


def prepend_notes(existing_notes, new_flag):
    """Prepend flag to existing notes without overwriting."""
    if existing_notes:
        return f"{new_flag}\n{existing_notes}"
    return new_flag


def update_contact_notes(contact_id, flag_text, existing_notes, dry_run=False):
    """Write flag to contact notes field (prepend only)."""
    new_notes = prepend_notes(existing_notes, flag_text)
    if dry_run:
        return True
    url = f"https://api.airtable.com/v0/{BASE_ID}/{CONTACTS_TABLE}/{contact_id}"
    resp = requests.patch(url, headers=AIRTABLE_HEADERS,
                          json={"fields": {CF_NOTES: new_notes}}, timeout=20)
    if not resp.ok:
        print(f"    ERROR updating notes: {resp.status_code} {resp.text[:100]}")
        return False
    return True


def create_placeholder_contact(name, title, company_id, email, tier, notes, dry_run=False):
    """Create a new Unverified placeholder contact record."""
    fields = {
        CF_FULL_NAME:    name,
        CF_TITLE:        title,
        CF_COMPANY_LINK: [company_id],
        CF_STATUS:       "Unverified",
        CF_TIER:         tier,
        CF_NOTES:        notes,
    }
    if email:
        fields[CF_EMAIL] = email
    if dry_run:
        return "dry-run-id"
    url = f"https://api.airtable.com/v0/{BASE_ID}/{CONTACTS_TABLE}"
    resp = requests.post(url, headers=AIRTABLE_HEADERS,
                         json={"fields": fields}, timeout=20)
    if not resp.ok:
        print(f"    ERROR creating contact: {resp.status_code} {resp.text[:100]}")
        return None
    return resp.json().get("id")


def update_company_hygiene_date(company_id, dry_run=False):
    """Set last_hygine_review to today."""
    if dry_run:
        return True
    url = f"https://api.airtable.com/v0/{BASE_ID}/{COMPANIES_TABLE}/{company_id}"
    resp = requests.patch(url, headers=AIRTABLE_HEADERS,
                          json={"fields": {CO_HYGIENE_DATE: TODAY}}, timeout=20)
    return resp.ok


# ─── Perplexity search ────────────────────────────────────────────────────────

def perplexity_search(query):
    """Run a web search via Perplexity. Returns text result or empty string."""
    payload = {
        "model": "sonar-pro",
        "messages": [
            {"role": "system", "content": "You are a research tool. Return factual, concise results only."},
            {"role": "user", "content": query},
        ],
        "temperature": 0.1,
        "max_tokens": 600,
    }
    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post("https://api.perplexity.ai/chat/completions",
                             headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return ""


# ─── Claude for parsing ───────────────────────────────────────────────────────

claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

def parse_contact_verification(search_result, contact_name, company_name, current_title):
    """Use Claude to parse search result and determine if contact needs flagging."""
    if not search_result:
        return {"flag": True, "reason": "unable_to_verify", "detail": "No search results found."}

    prompt = f"""A sales rep is verifying whether a contact is still in their current role.

Contact: {contact_name}
Current company on file: {company_name}
Current title on file: {current_title}

Web search result:
{search_result[:1000]}

Respond with JSON only:
{{
  "flag": true/false,
  "reason": "left_company" | "title_changed" | "unable_to_verify" | "all_clear",
  "new_company": "company if left" or null,
  "new_title": "new title if changed" or null,
  "detail": "one sentence explanation"
}}

Rules:
- flag=false and reason=all_clear if they appear to still be at {company_name} in same or similar role
- flag=true if they clearly left, changed title significantly, or can't be verified
- When in doubt, flag=true with reason=unable_to_verify"""

    try:
        msg = claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        if "{" in text:
            return json.loads(text[text.index("{"):text.rindex("}")+1])
    except Exception:
        pass
    return {"flag": True, "reason": "unable_to_verify", "detail": "Parse error — manual check recommended."}


def parse_new_contacts(search_result, company_name, existing_contacts):
    """Use Claude to extract new ICP contacts from search results."""
    if not search_result:
        return []

    existing_names = [
        r.get("fields", {}).get("full_name", "").lower()
        for r in existing_contacts
    ]

    prompt = f"""Extract ICP contacts from this search result for {company_name}.

Search result:
{search_result[:1500]}

Existing contacts already in CRM (skip these):
{', '.join(existing_names[:20]) if existing_names else 'none'}

Extract only people who:
1. Currently work at {company_name}
2. Have titles related to: M&A, corp dev, legal (GC/counsel), CFO, finance, integration, business development, corporate strategy

Return JSON array only (empty array if none found):
[
  {{
    "name": "Full Name",
    "title": "Current Title",
    "source": "brief source description"
  }}
]"""

    try:
        msg = claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        if "[" in text:
            return json.loads(text[text.index("["):text.rindex("]")+1])
    except Exception:
        pass
    return []


# ─── Duplicate check ──────────────────────────────────────────────────────────

def is_duplicate(new_name, existing_contacts):
    """Check if new_name matches an existing contact (name variant aware)."""
    parts = new_name.strip().split()
    if len(parts) < 2:
        return False
    new_first = normalize_first_name(parts[0])
    new_last = parts[-1].lower()

    for rec in existing_contacts:
        existing_name = rec.get("fields", {}).get("full_name", "")
        eparts = existing_name.strip().split()
        if len(eparts) < 2:
            continue
        e_first = normalize_first_name(eparts[0])
        e_last = eparts[-1].lower()
        if new_last == e_last and new_first == e_first:
            return True
    return False


# ─── New contact searches (7 per company) ────────────────────────────────────

NEW_CONTACT_SEARCHES = [
    '{company} "corporate development" OR "corp dev" OR "M&A" OR "mergers and acquisitions" OR "integration" OR "M&A integration" OR "post-merger integration" OR "head of integration"',
    '{company} "general counsel" OR "chief legal officer" OR "CLO" OR "deputy general counsel" OR "associate general counsel" OR "head of legal"',
    '{company} "M&A counsel" OR "antitrust counsel" OR "securities counsel" OR "transactions counsel" OR "corporate counsel" OR "deal counsel" OR "IP counsel"',
    '{company} "chief financial officer" OR "VP finance" OR "strategic finance" OR "treasury" OR "investor relations" OR "corporate strategy" OR "strategic planning"',
    '{company} "business development" OR "strategic partnerships" OR "chief business development" "acquisitions" OR "licensing" OR "investments" OR "JV"',
    '{company} "chief of staff" OR "managing director" OR "investment" OR "portfolio" OR "venture"',
    '{company} employees "investment banking" OR "private equity" OR "venture capital"',
]


def find_new_contacts(company_name, company_id, existing_contacts, dry_run=False):
    """Run 7 searches, extract new ICP contacts, create placeholders."""
    new_contacts_added = []
    domain = guess_domain(company_name)

    for i, search_template in enumerate(NEW_CONTACT_SEARCHES):
        query = search_template.format(company=company_name)
        result = perplexity_search(query)
        time.sleep(0.5)

        if not result:
            continue

        candidates = parse_new_contacts(result, company_name, existing_contacts)

        for candidate in candidates:
            name = candidate.get("name", "").strip()
            title = candidate.get("title", "").strip()
            source = candidate.get("source", f"Search {i+1}")

            if not name or not title:
                continue

            # Duplicate check
            if is_duplicate(name, existing_contacts):
                continue

            # Also check against contacts we just added this run
            already_added_names = [c["name"] for c in new_contacts_added]
            if any(is_duplicate(name, [{"fields": {"full_name": n}}]) for n in already_added_names):
                continue

            # Construct email
            parts = name.split()
            if len(parts) >= 2 and domain:
                first = parts[0].lower()
                last = parts[-1].lower()
                constructed_email = f"{first}.{last}@{domain}"
            else:
                constructed_email = ""

            tier = assign_tier(title)
            notes = (
                f"🆕 NEW CONTACT {TODAY}: Found via web search. "
                f"Source: {source}. "
                f"Email constructed as {constructed_email if constructed_email else '[unknown domain]'} — verify before sending. "
                f"Pending Shelle review. Auto-created by hygiene task."
            )

            record_id = create_placeholder_contact(
                name=name,
                title=title,
                company_id=company_id,
                email=constructed_email,
                tier=tier,
                notes=notes,
                dry_run=dry_run,
            )

            if record_id:
                new_contacts_added.append({
                    "name": name,
                    "title": title,
                    "email": constructed_email,
                    "tier": tier,
                    "record_id": record_id,
                })
                print(f"      🆕 New: {name} ({title}) [{tier}]")

                # Add to existing_contacts to prevent dupes on subsequent searches
                existing_contacts.append({"fields": {"full_name": name, "tier": tier}})

    return new_contacts_added


# ─── Gmail summary ────────────────────────────────────────────────────────────

def send_hygiene_summary(results, dry_run=False):
    """Send hygiene summary email to Shelle's Datasite inbox."""
    total_companies = len(results)
    total_flagged = sum(len(r["flagged"]) for r in results)
    total_new = sum(len(r["new_contacts"]) for r in results)
    all_clear = sum(1 for r in results if not r["flagged"] and not r["new_contacts"])

    lines = [f"ShelleOS Hygiene Review — {TODAY}\n"]

    for r in results:
        company = r["company_name"]
        flagged = r["flagged"]
        new_contacts = r["new_contacts"]

        if not flagged and not new_contacts:
            continue

        lines.append(f"COMPANY: {company}")
        for f in flagged:
            lines.append(f"  🔴 {f['name']} ({f['title']}) — {f['detail']}")
        for n in new_contacts:
            lines.append(f"  🆕 NEW: {n['name']}, {n['title']} [{n['tier']}] — {n['email'] or 'no email'}")
        lines.append("")

    lines.append(f"{'='*50}")
    lines.append(f"SUMMARY: {total_companies} companies reviewed | {total_flagged} contacts flagged | {total_new} new contacts added to Review Queue")
    if all_clear > 0:
        lines.append(f"{all_clear} companies — all contacts verified, no changes")

    body = "\n".join(lines)
    subject = f"ShelleOS Hygiene Review — {TODAY}"

    if dry_run:
        print(f"\n{'='*60}")
        print(f"HYGIENE SUMMARY EMAIL (DRY RUN)")
        print(f"TO: {GMAIL_TO}")
        print(f"SUBJECT: {subject}")
        print(f"\n{body}")
        return

    msg = MIMEMultipart()
    msg["From"]    = GMAIL_FROM
    msg["To"]      = GMAIL_TO
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_FROM, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        print(f"\n✅ Hygiene summary sent to {GMAIL_TO}")
    except Exception as e:
        print(f"\nERROR sending summary email: {e}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ShelleOS Contact Hygiene Scanner")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print changes without writing to Airtable or sending email")
    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    print(f"\nShelleOS Contact Hygiene Scanner — {TODAY} [{mode}]")
    print("=" * 60)

    # Step 1: Select 20 companies to review
    print("Fetching 20 companies to review (oldest hygiene date first)...")
    companies = get_companies_to_review(n=20)
    print(f"  {len(companies)} companies selected\n")

    results = []

    for company_rec in companies:
        company_id   = company_rec["id"]
        company_name = company_rec.get("fields", {}).get("Company Name", "Unknown")

        print(f"── {company_name}")

        # Step 2: Fetch active contacts (for verification) + all contacts (for dedup)
        contacts     = get_contacts_for_company(company_rec, active_only=True)
        all_contacts = get_contacts_for_company(company_rec, active_only=False)
        print(f"   {len(contacts)} active contacts ({len(all_contacts)} total)")

        flagged_contacts = []

        # Step 3: Verify each contact
        for contact in contacts:
            f = contact.get("fields", {})
            name  = f.get("full_name", "").strip()
            title = f.get("title", "").strip()
            existing_notes = f.get("notes", "") or ""

            if not name:
                continue

            query = f'"{name}" "{company_name}" {title} LinkedIn'
            result = perplexity_search(query)
            time.sleep(0.5)

            parsed = parse_contact_verification(result, name, company_name, title)

            if not parsed.get("flag"):
                continue  # All clear — do NOT write to Airtable

            # Build flag text
            reason = parsed.get("reason", "unable_to_verify")
            detail = parsed.get("detail", "")
            new_company = parsed.get("new_company", "")
            new_title_found = parsed.get("new_title", "")

            if reason == "left_company" and new_company:
                flag = f"🔴 REVIEW {TODAY}: May have left {company_name}. LinkedIn shows {new_title_found or 'new role'} at {new_company}."
            elif reason == "title_changed" and new_title_found:
                flag = f"🔴 REVIEW {TODAY}: Title change — now {new_title_found} (was {title})."
            elif reason == "unable_to_verify":
                flag = f"🔴 REVIEW {TODAY}: Unable to verify — {detail}"
            else:
                flag = f"🔴 REVIEW {TODAY}: {detail}"

            update_contact_notes(contact["id"], flag, existing_notes, dry_run=args.dry_run)
            print(f"   🔴 {name} — {flag[:80]}")

            flagged_contacts.append({
                "name": name,
                "title": title,
                "detail": detail,
                "flag": flag,
            })

        # Step 4: Search for new ICP contacts (dedup against ALL contacts, not just active)
        print(f"   Searching for new ICP contacts (7 searches)...")
        new_contacts = find_new_contacts(company_name, company_id, all_contacts, dry_run=args.dry_run)

        # Step 6: Update company hygiene date
        update_company_hygiene_date(company_id, dry_run=args.dry_run)
        if not args.dry_run:
            print(f"   ✅ Hygiene date updated")

        results.append({
            "company_name": company_name,
            "contacts_reviewed": len(contacts),
            "flagged": flagged_contacts,
            "new_contacts": new_contacts,
        })

        time.sleep(0.5)

    # Step 7: Send Gmail summary
    print(f"\n{'='*60}")
    total_flagged = sum(len(r["flagged"]) for r in results)
    total_new = sum(len(r["new_contacts"]) for r in results)
    print(f"Scan complete: {len(companies)} companies | {total_flagged} flagged | {total_new} new contacts")

    send_hygiene_summary(results, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
