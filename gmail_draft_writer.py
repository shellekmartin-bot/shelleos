#!/usr/bin/env python3
"""
ShelleOS Gmail Draft Writer
For each new trigger, finds the top ICP contact and sends a drafted outreach email
FROM shelle.k.martin@gmail.com TO shelle.martin@datasite.com.

Each email contains: ICP info, PATH (cold vs past client), subject, email draft, voicemail script.
Shelle reviews and sends. Nothing goes to contacts automatically.

Usage:
  python3 gmail_draft_writer.py           # draft for last 7 days of triggers
  python3 gmail_draft_writer.py --dry-run # print drafts, don't send
  python3 gmail_draft_writer.py --days 3  # override lookback window

Setup:
  pip install requests python-dotenv anthropic
"""

import os
import sys
import time
import argparse
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from dotenv import load_dotenv
import anthropic

# ─── Load credentials ─────────────────────────────────────────────────────────
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
    print(f"\nERROR: Missing keys in .env: {', '.join(missing)}")
    sys.exit(1)

# ─── Airtable config ──────────────────────────────────────────────────────────
BASE_ID          = "app9VxYkYesBpA7Fe"
TRIGGERS_TABLE   = "tblWehiTOEQf5EnHt"
CONTACTS_TABLE   = "tblndO8S7Nnqmsenh"
COMPANIES_TABLE  = "tblBImf6yfRbzSB4e"

AIRTABLE_HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_API_KEY}",
    "Content-Type": "application/json",
}

# Trigger field names
TF_HEADLINE     = "trigger_headline"
TF_NOTES        = "Notes"
TF_TYPE         = "trigger_type"
TF_STATUS       = "Status"
TF_ACTION_TAKEN = "action_taken"
TF_COMPANY      = "company"
TF_DATE         = "trigger_date"

# Contact field names
CF_FULL_NAME    = "full_name"
CF_TITLE        = "title"
CF_EMAIL        = "email"
CF_TIER         = "tier"
CF_STATUS       = "contact_status"
CF_NO_LONGER    = "no_longer_there"
CF_COMPANY      = "company"
CF_DS_ADMIN     = "N_DS_Admin"
CF_DS_USERS     = "N_Total_DS_User"
CF_CONTRACT_SGN = "datasite_contract_signer"

# Company field IDs
CO_NAME         = "fldrIHICJ7fN6G87M"
CO_PAST_CLIENT  = "fldoGkmGXgBXc8SS5"
CO_ACQUIRER     = "fldRuC4Q7Ww0624m5"
CO_STRATEGIC    = "fldDZKoQ6IfCJSQyN"
CO_NOTES        = "fld9vh8BDM4dxTrBA"

# Trigger type priority order (1 = highest)
TRIGGER_PRIORITY = {
    "Acquision": 1,
    "Sell Side ": 1,
    "Funding ": 2,
    "Leadership Change": 3,
    "News": 4,
    "Earnings ": 5,
    "Activist Activity": 6,
}

# ─── Airtable helpers ──────────────────────────────────────────────────────────

def get_triggers(days_back=7):
    """Pull triggers created in the last N days with Status=Todo and action_taken=false."""
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    url = f"https://api.airtable.com/v0/{BASE_ID}/{TRIGGERS_TABLE}"
    params = {
        "filterByFormula": f"AND({{trigger_date}} >= '{cutoff}', {{Status}} = 'Todo', NOT({{action_taken}}))",
        "pageSize": 100,
    }
    triggers = []
    offset = None
    while True:
        if offset:
            params["offset"] = offset
        resp = requests.get(url, headers=AIRTABLE_HEADERS, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        triggers.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            break
        time.sleep(0.2)
    return triggers


def get_company(company_record_id):
    """Fetch company record fields."""
    url = f"https://api.airtable.com/v0/{BASE_ID}/{COMPANIES_TABLE}/{company_record_id}"
    resp = requests.get(url, headers=AIRTABLE_HEADERS, timeout=20)
    if not resp.ok:
        return {}
    return resp.json().get("fields", {})


def get_top_contact(company_record_id):
    """
    Find the best T1/T2 ICP contact at this company.
    Returns dict with contact info or None.
    """
    company_url = f"https://api.airtable.com/v0/{BASE_ID}/{COMPANIES_TABLE}/{company_record_id}"
    resp = requests.get(company_url, headers=AIRTABLE_HEADERS, timeout=20)
    if not resp.ok:
        return None
    contact_ids = resp.json().get("fields", {}).get("Contacts", [])
    if not contact_ids:
        return None

    contact_ids = contact_ids[:20]
    formula_parts = [f"RECORD_ID() = '{cid}'" for cid in contact_ids]
    formula = f"OR({', '.join(formula_parts)})"

    url = f"https://api.airtable.com/v0/{BASE_ID}/{CONTACTS_TABLE}"
    params = {"filterByFormula": formula, "pageSize": 50}
    resp = requests.get(url, headers=AIRTABLE_HEADERS, params=params, timeout=20)
    if not resp.ok:
        return None
    all_records = resp.json().get("records", [])

    # Filter to callable Active contacts
    def is_callable(rec):
        f = rec.get("fields", {})
        if f.get(CF_NO_LONGER):
            return False
        status = (f.get(CF_STATUS) or "").strip()
        if status in ("Do not call", "Do not call "):
            return False
        # Require Active status (Unverified contacts excluded)
        if status and status not in ("Active", ""):
            return False
        return True

    callable_records = [r for r in all_records if is_callable(r)]

    t1 = [r for r in callable_records if r.get("fields", {}).get(CF_TIER) == "T1"]
    t2 = [r for r in callable_records if r.get("fields", {}).get(CF_TIER) == "T2"]
    records = t1 if t1 else t2

    if not records:
        return None

    def score_contact(rec):
        f = rec.get("fields", {})
        score = 0
        if f.get(CF_CONTRACT_SGN):
            score += 25
        admins = f.get(CF_DS_ADMIN, 0) or 0
        users  = f.get(CF_DS_USERS, 0) or 0
        if admins >= 3:
            score += 25
        elif admins > 0:
            score += 15
        if users >= 15:
            score += 30
        elif users >= 5:
            score += 20
        elif users >= 1:
            score += 10
        title = (f.get(CF_TITLE) or "").lower()
        if any(x in title for x in ["corp dev", "corporate dev", "corporate development", "corpdev"]):
            score += 12
        elif any(x in title for x in [
            "general counsel", "chief legal", "clo", "head of legal",
            "deputy general counsel", "deputy gc", "associate general counsel",
            "associate gc", "assistant general counsel", "assistant gc",
            "vp legal", "vp, legal", "vice president legal", "senior counsel",
        ]) or title.strip().lower() in ("gc", "general counsel", "clo"):
            score += 10
        elif any(x in title for x in ["chief business officer", "cbo", "licensing", "alliance"]):
            score += 10
        elif "cfo" in title or "chief financial" in title:
            score += 8
        elif "ceo" in title or "chief executive" in title:
            score += 5
        elif "counsel" in title:
            score += 4
        return score

    records.sort(key=score_contact, reverse=True)
    best = records[0].get("fields", {})
    full_name = best.get(CF_FULL_NAME, "").strip()
    if not full_name:
        return None

    return {
        "full_name": full_name,
        "first_name": full_name.split()[0],
        "last_name": full_name.split()[-1],
        "title": best.get(CF_TITLE, "").strip(),
        "email": best.get(CF_EMAIL, "").strip(),
    }


# ─── Email + VM generation ────────────────────────────────────────────────────

VOICE_PROMPT = """You are writing outreach for Shelle Martin, Enterprise Sales Manager at Datasite (Bay Area M&A platform). She has 2,300+ deals. She writes like a peer who has been at this 20 years and has nothing to prove. Chill, sharp, Bay Area. Not a consultant. Not a rep.

TRIGGER:
Company: {company}
Signal: {headline}
Type: {trigger_type}
Context: {notes}

CONTACT:
Name: {first_name} {last_name}
Title: {title}

OUTREACH PATH: {path}
{path_note}

TRIGGER-TYPE ANGLE:
{type_angle}

WRITE THE EMAIL — NON-NEGOTIABLE RULES:
1. BLUF: bottom line in sentence one. No warmup. No throat-clearing.
2. ABT structure internally (never show it): setup AND context, BUT tension/trigger, THEREFORE ask.
3. 5-6 short sentences max. Emails fit one iPhone screen.
4. Subject line: lowercase, conversational, peer-to-peer. Not campaign-sent.
5. Hard CTA with easy exit: "Worth a 15?" or "I'm down the street Tuesday — you have 20 minutes?" or "Not the right time?"
6. Sign off: just "Shelle"

BANNED — if any appear, rewrite:
- Em dashes (—) anywhere, ever
- "just", "leverage", "landscape", "strategic alignment", "stakeholder"
- "I hope this finds you well" / "I wanted to reach out" / "As you know..."
- "Feel free to..." / "whenever works" / "would love to connect" / "happy to chat"
- Soft CTAs of any kind
- Explaining their industry back to them
- Invented details not in the trigger or context

THEN write a 20-second voicemail script. Same voice, same hook, shorter. End with: "I'll shoot you an email too."

FORMAT YOUR RESPONSE EXACTLY (nothing else):
SUBJECT: [subject line]

[email body]

Shelle

VM:
[20-second voicemail script — same hook, shorter, same voice. End with: "I'll shoot you an email too."]

LI:
[LinkedIn DM — 2 sentences max. Same hook, casual, peer-to-peer. No pitch. Just the reason you're reaching out and one soft door-opener. No em dashes, no banned words.]"""


def build_type_angle(trigger_type, headline, notes):
    t = (trigger_type or "").strip().lower()
    h = (headline or "").lower()
    if any(x in t for x in ["acqui", "sell side"]) or any(x in h for x in ["acqui", "merger", "divest"]):
        return 'Acquisition angle: lead with "saw the [target] deal." Position Datasite as the VDR already in their stack for the next one.'
    if "funding" in t or any(x in h for x in ["funding", "raise", "round", "ipo", "debt", "series"]):
        if any(x in h for x in ["debt", "pipe", "credit", "exploring"]):
            return 'Funding angle: lead with "saw you\'re exploring options." Understated, not congratulatory.'
        return 'Funding angle: lead with "congrats on the round." Pivot to the deal activity that typically follows.'
    if "leadership" in t:
        return 'Leadership angle: lead with "saw the change at [role]" or "saw [name] joined." Position as a resource for the new person, not a sales pitch.'
    if "earn" in t:
        return 'Earnings angle: lead with empathy, not opportunism. "Market has been tough on [company]" not "your stock is down." Never kick someone when they\'re down.'
    if "news" in t or "partner" in h:
        return 'Partnership angle: lead with "saw the [partner] deal." Note that partnerships often signal M&A activity and offer to be a resource.'
    return 'News angle: lead with the specific signal. Keep it grounded in what actually happened.'


def build_path_note(is_past_client):
    if is_past_client:
        return (
            "PATH B — PAST CLIENT. Lead with the MSA hook FIRST: "
            "they are already set up on Datasite, no procurement, no redlines, live in minutes. "
            "Use the trigger as the second beat."
        )
    return "PATH A — COLD OUTREACH. Lead with the trigger as the hook. No MSA angle."


def generate_draft(company, headline, notes, trigger_type, first_name, last_name, title, is_past_client):
    """Call Claude to generate email + voicemail draft."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    path   = "Past Client (PATH B)" if is_past_client else "Cold Outreach (PATH A)"
    prompt = VOICE_PROMPT.format(
        company=company,
        headline=headline,
        notes=notes or "",
        trigger_type=trigger_type,
        first_name=first_name,
        last_name=last_name,
        title=title or "",
        path=path,
        path_note=build_path_note(is_past_client),
        type_angle=build_type_angle(trigger_type, headline, notes),
    )
    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text.strip()


# ─── Email sending ────────────────────────────────────────────────────────────

def send_draft(contact, company, trigger, draft_text, is_past_client, dry_run=False):
    """Send one drafted outreach email to Shelle's Datasite inbox."""
    fields       = trigger.get("fields", {})
    trigger_type = fields.get(TF_TYPE, "").strip()
    headline     = fields.get(TF_HEADLINE, "")
    path_label   = "Past Client" if is_past_client else "Cold"

    # Parse subject from draft
    subject_line = f"Draft: {company}"
    email_body   = draft_text
    vm_script    = ""

    lines = draft_text.split("\n")
    subject_found = False
    vm_start = None

    for i, line in enumerate(lines):
        if line.upper().startswith("SUBJECT:") and not subject_found:
            subject_line = line.replace("SUBJECT:", "").replace("Subject:", "").strip()
            subject_line = f"Draft [{path_label}]: {subject_line} [{company}]"
            lines = lines[i + 1:]
            subject_found = True
            break

    # Split email body, VM script, and LinkedIn DM
    vm_script = ""
    li_dm     = ""
    for i, line in enumerate(lines):
        if line.strip().upper().startswith("VM:"):
            rest = lines[i + 1:]
            email_body = "\n".join(lines[:i]).strip()
            # Check if LI section follows
            li_start = None
            for j, l in enumerate(rest):
                if l.strip().upper().startswith("LI:"):
                    li_start = j
                    break
            if li_start is not None:
                vm_script = "\n".join(rest[:li_start]).strip()
                li_dm     = "\n".join(rest[li_start + 1:]).strip()
            else:
                vm_script = "\n".join(rest).strip()
            break
    else:
        email_body = "\n".join(lines).strip()

    # Build contact header — clean, copy-paste ready
    if contact:
        name_line  = f"NAME:    {contact['full_name']} | {contact.get('title', '')}"
        email_line = f"EMAIL:   {contact.get('email', 'no email on file')}"
    else:
        name_line  = "NAME:    No contact on file — find the right person before sending"
        email_line = "EMAIL:   —"

    full_message = (
        f"{name_line}\n"
        f"{email_line}\n"
        f"SUBJECT: {subject_line.replace(f'Draft [{path_label}]: ', '').replace(f' [{company}]', '')}\n"
        f"PATH:    {path_label} | {trigger_type} — {headline}\n"
        f"\n{'='*50}\n\n"
        f"{email_body}\n\n"
        f"{'='*50}\n"
        f"VM:\n{vm_script}\n\n"
        f"{'='*50}\n"
        f"LINKEDIN:\n{li_dm}\n\n"
        f"{'='*50}\n"
        f"ShelleOS | {datetime.now().strftime('%B %d, %Y')}"
    )

    if dry_run:
        print(f"\n{'='*60}")
        print(f"TO: {GMAIL_TO}")
        print(f"SUBJECT: {subject_line}")
        print(f"\n{full_message}")
        return True

    msg = MIMEMultipart()
    msg["From"]    = GMAIL_FROM
    msg["To"]      = GMAIL_TO
    msg["Subject"] = subject_line
    msg.attach(MIMEText(full_message, "plain"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_FROM, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"    ERROR sending email: {e}")
        return False


def send_quiet_morning(dry_run=False):
    """Send 'quiet morning' email when there are no new triggers."""
    today   = datetime.now().strftime("%B %d, %Y")
    subject = f"Trigger Drafter — {today} — No new triggers"
    body    = f"No new triggers today. Quiet morning.\n\nShelleOS | {today}"

    if dry_run:
        print(f"\nWould send: {subject}")
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
    except Exception as e:
        print(f"    ERROR sending quiet morning email: {e}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ShelleOS Gmail Draft Writer")
    parser.add_argument("--dry-run", action="store_true", help="Print drafts without sending")
    parser.add_argument("--days", type=int, default=3, help="Lookback window in days (default: 3 / 72 hours)")
    args = parser.parse_args()

    today = datetime.now().strftime("%Y-%m-%d")
    mode  = "DRY RUN" if args.dry_run else "LIVE"
    print(f"\nShelleOS Gmail Draft Writer — {today} [{mode}]")
    print("=" * 60)

    print(f"Fetching triggers from last {args.days} day(s)...")
    try:
        triggers = get_triggers(days_back=args.days)
    except Exception as e:
        print(f"ERROR: Could not fetch triggers: {e}")
        sys.exit(1)

    if not triggers:
        print("No new triggers found. Sending quiet morning email.")
        send_quiet_morning(dry_run=args.dry_run)
        return

    print(f"  {len(triggers)} trigger(s) found")

    # Sort by priority (acquisition first, etc.)
    def trigger_priority(t):
        ttype = t.get("fields", {}).get(TF_TYPE, "").strip()
        return TRIGGER_PRIORITY.get(ttype, 99)

    triggers.sort(key=trigger_priority)

    # Cap at 10 — note the rest
    queued = []
    if len(triggers) > 10:
        queued   = triggers[10:]
        triggers = triggers[:10]
        print(f"  Drafting top 10 by priority. {len(queued)} queued for tomorrow.")

    sent    = 0
    skipped = 0
    skip_log = []
    type_counts = {}

    for trigger in triggers:
        fields        = trigger.get("fields", {})
        headline      = fields.get(TF_HEADLINE, "")
        notes         = fields.get(TF_NOTES, "")
        ttype         = fields.get(TF_TYPE, "News")
        company_links = fields.get(TF_COMPANY, [])

        if not company_links:
            reason = f"no company link — {headline[:60]}"
            print(f"  SKIP: {reason}")
            skip_log.append(reason)
            skipped += 1
            continue

        company_id   = company_links[0]
        company_data = get_company(company_id)
        company_name = company_data.get("Company Name", "Unknown Company")
        is_past_client = bool(company_data.get("bucket_past_client"))

        # Get top ICP contact (None = no contact on file — still draft)
        contact = get_top_contact(company_id)

        if contact and not contact.get("email"):
            # Has a contact but no email — still draft, flag it
            contact["email"] = "no email on file"

        contact_display = contact["full_name"] if contact else "No contact on file"
        print(f"  Drafting: {company_name} → {contact_display}")

        try:
            draft = generate_draft(
                company=company_name,
                headline=headline,
                notes=notes,
                trigger_type=ttype,
                first_name=contact["first_name"] if contact else "there",
                last_name=contact["last_name"] if contact else "",
                title=contact.get("title", "") if contact else "",
                is_past_client=is_past_client,
            )
        except Exception as e:
            reason = f"draft generation failed — {company_name}: {e}"
            print(f"    ERROR: {e}")
            skip_log.append(reason)
            skipped += 1
            continue

        success = send_draft(contact, company_name, trigger, draft, is_past_client, dry_run=args.dry_run)
        if success:
            if not args.dry_run:
                print(f"  SENT: {company_name} / {contact_display} — {GMAIL_TO}")
            sent += 1
            type_counts[ttype.strip()] = type_counts.get(ttype.strip(), 0) + 1
        else:
            skipped += 1

        time.sleep(1)

    # Summary
    print("\n" + "=" * 60)
    print(f"Done: {sent} drafted | {skipped} skipped")
    if type_counts:
        print("Drafts by type:")
        for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
            print(f"  {t}: {c}")
    if skip_log:
        print("Skipped:")
        for s in skip_log:
            print(f"  - {s}")
    if queued:
        print(f"\n{len(queued)} triggers queued (over the 10-draft cap):")
        for q in queued:
            h = q.get("fields", {}).get(TF_HEADLINE, "")[:60]
            print(f"  - {h}")
    if args.dry_run:
        print("\nDRY RUN — nothing was sent.")
    print()


if __name__ == "__main__":
    main()
