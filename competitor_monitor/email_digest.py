#!/usr/bin/env python3
"""
Competitor Intel Digest Email Formatter & Sender
Formats new signals into HTML email and sends to Shelle at 6 AM Pacific.
"""

import os
import sys
import sqlite3
import smtplib
from datetime import datetime, date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

# ─── Credentials ──────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(SCRIPT_DIR, '..', '.env'))

GMAIL_FROM = os.getenv("GMAIL_FROM")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
GMAIL_TO = os.getenv("GMAIL_TO", "shelle.martin@datasite.com")

missing = [k for k, v in {
    "GMAIL_FROM": GMAIL_FROM,
    "GMAIL_APP_PASSWORD": GMAIL_APP_PASSWORD,
}.items() if not v]
if missing:
    print(f"ERROR: Missing env vars: {', '.join(missing)}")
    sys.exit(1)

DB_PATH = os.path.join(SCRIPT_DIR, "data", "signals.db")
TODAY = date.today().isoformat()

# ─── Signal type icons & colors ───────────────────────────────────────────────
SIGNAL_ICONS = {
    "feature": "✨",
    "pricing": "💰",
    "employee": "👥",
    "compliance": "⚖️",
    "partnership": "🤝",
    "news": "📰",
}

SIGNAL_COLORS = {
    "feature": "#2563eb",
    "pricing": "#dc2626",
    "employee": "#f59e0b",
    "compliance": "#7c3aed",
    "partnership": "#059669",
    "news": "#6b7280",
}

def _esc(text):
    """Minimal HTML escaping."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def fetch_signals():
    """Get all unemailed signals from DB, grouped by competitor."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT competitor, signal_type, headline, url, why_relevant, rank, published_date
        FROM signals
        WHERE emailed IS NULL
        ORDER BY competitor, rank
    """)
    rows = c.fetchall()
    conn.close()

    # Group by competitor
    signals_by_competitor = {}
    for row in rows:
        competitor = row[0]
        if competitor not in signals_by_competitor:
            signals_by_competitor[competitor] = []
        signals_by_competitor[competitor].append({
            "signal_type": row[1],
            "headline": row[2],
            "url": row[3],
            "why_relevant": row[4],
            "rank": row[5],
            "published_date": row[6],
        })

    return signals_by_competitor

def format_email_html(signals_by_competitor):
    """Format signals into HTML email."""
    date_fmt = datetime.strptime(TODAY, "%Y-%m-%d").strftime("%A, %B %-d, %Y")
    total_signals = sum(len(sigs) for sigs in signals_by_competitor.values())

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;">
<tr><td align="center" style="padding:32px 16px;">
<table width="640" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%;">

  <!-- HEADER -->
  <tr><td style="background:linear-gradient(135deg, #1e1b4b 0%, #312e81 100%);border-radius:12px 12px 0 0;padding:32px 28px;text-align:center;">
    <div style="color:#fff;font-size:24px;font-weight:700;letter-spacing:-0.5px;margin-bottom:8px;">
      Competitor Intel Digest
    </div>
    <div style="color:#c7d2fe;font-size:14px;">
      {date_fmt} &nbsp;|&nbsp; <strong>{total_signals} signal{'s' if total_signals != 1 else ''}</strong>
    </div>
  </td></tr>

  <!-- BODY -->
  <tr><td style="background:#fff;padding:32px 28px;border-radius:0 0 12px 12px;">
"""

    if not total_signals:
        html += """
    <p style="color:#6b7280;font-size:15px;line-height:1.6;">
      Quiet day — no significant competitive signals detected across Intralinks &amp; Venue.
    </p>
"""
    else:
        for competitor in ["Intralinks", "Venue"]:
            if competitor not in signals_by_competitor or not signals_by_competitor[competitor]:
                continue

            sigs = signals_by_competitor[competitor]
            html += f"""
    <div style="margin-bottom:32px;">
      <div style="font-size:18px;font-weight:700;color:#1f2937;margin-bottom:16px;">
        {competitor}
      </div>
"""
            # Group by signal type
            by_type = {}
            for sig in sigs:
                stype = sig["signal_type"]
                if stype not in by_type:
                    by_type[stype] = []
                by_type[stype].append(sig)

            for stype in sorted(by_type.keys()):
                type_sigs = by_type[stype]
                icon = SIGNAL_ICONS.get(stype, "📌")
                color = SIGNAL_COLORS.get(stype, "#6b7280")

                html += f"""
      <div style="margin-bottom:20px;">
        <div style="font-size:13px;font-weight:700;color:{color};text-transform:uppercase;letter-spacing:0.5px;margin-bottom:10px;">
          {icon} {stype.replace('_', ' ')} ({len(type_sigs)})
        </div>
"""
                for sig in type_sigs:
                    headline_esc = _esc(sig["headline"])
                    why_esc = _esc(sig["why_relevant"])
                    url = sig["url"] or "#"

                    html += f"""
        <div style="background:#f9fafb;border-left:3px solid {color};padding:12px 14px;margin-bottom:8px;border-radius:4px;">
          <div style="margin-bottom:6px;">
            <a href="{url}" style="color:#1f2937;text-decoration:none;font-weight:600;font-size:14px;">
              {headline_esc}
            </a>
          </div>
          <div style="font-size:13px;color:#6b7280;line-height:1.5;">
            {why_esc}
          </div>
        </div>
"""
                html += "      </div>\n"

            html += "    </div>\n"

    # ── Footer ────────────────────────────────────────────────────────────────
    html += f"""
    <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0 16px 0;">
    <p style="font-size:12px;color:#9ca3af;margin:0;line-height:1.6;">
      Powered by Firecrawl + Claude Haiku | ShelleOS
      <br>Next digest: {(datetime.strptime(TODAY, '%Y-%m-%d').replace(hour=6)).strftime('%A, %B %-d at %I:%M %p PT')}
    </p>

  </td></tr>
</table>
</td></tr>
</table>
</body></html>
"""

    return html

def send_email(email_html):
    """Send HTML email via Gmail SMTP."""
    msg = MIMEMultipart("alternative")
    msg["From"] = GMAIL_FROM
    msg["To"] = GMAIL_TO
    msg["Subject"] = f"Competitor Intel Digest — {TODAY}"
    msg.attach(MIMEText(email_html, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_FROM, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        print(f"✅ Email sent to {GMAIL_TO}")
        return True
    except Exception as e:
        print(f"❌ Email send failed: {e}")
        return False

def mark_signals_emailed():
    """Mark all emailed signals in DB."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    timestamp = datetime.now().isoformat()
    c.execute("UPDATE signals SET emailed = ? WHERE emailed IS NULL", (timestamp,))
    conn.commit()
    conn.close()

def main():
    print(f"\nFormatting and sending email digest...")

    signals = fetch_signals()

    if not signals or sum(len(v) for v in signals.values()) == 0:
        print("No new signals to email.")
        return

    html = format_email_html(signals)
    if send_email(html):
        mark_signals_emailed()
    else:
        print("Email failed — NOT marking signals as emailed.")

if __name__ == "__main__":
    main()
