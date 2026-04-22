# Deployment notes — Datasite Proposal Builder

This is a small Flask app that generates a PowerPoint proposal deck from a web
form. It is **intended for internal use only, behind SSO**. Nothing in the app
itself performs authentication — the reverse proxy / SSO layer in front of it
is expected to require login before any request reaches the app.

---

## What to hand to IT

- The repo folder (all `.pptx` templates, `app.py`, `index.html`,
  `requirements.txt`).
- This file.
- The URL path they should proxy to (usually `/`).

---

## Runtime requirements

- Python **3.9+** (tested on 3.9 and 3.11).
- Outbound HTTPS access to `logo.clearbit.com` (optional — used for
  auto-fetching company logos). If this is blocked, the form still works;
  reps just upload a logo manually.
- No database, no disk writes. The app holds state only in-memory (a small
  logo cache).

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run in production (gunicorn)

```bash
gunicorn -w 4 -b 0.0.0.0:${PORT:-5050} --timeout 60 app:app
```

- `-w 4` → four worker processes. Bump to 8+ for heavier teams; each deck
  generation holds a worker for ~2–5 seconds.
- `--timeout 60` → gives python-pptx headroom on big templates.
- The app reads `PORT` from the environment; default is `5050`.

## Run for quick local testing

```bash
python3 app.py
# → http://localhost:5050
```

Flask's built-in server is single-threaded; use gunicorn for anything shared.

## Health check

`GET /healthz` returns `{"ok": true}` when all four `.pptx` templates are
present. Returns HTTP 500 with `{"ok": false, "missing_templates": [...]}`
otherwise. Point your load balancer at this.

## SSO integration

The app does not perform auth. Put it behind your standard SSO reverse proxy
(Okta, Auth0, Azure AD, oauth2-proxy, etc.).

For per-user audit logging, make sure the proxy forwards the authenticated
user's email or username in **one** of these headers:

- `X-Forwarded-User`
- `X-Remote-User`
- `X-Auth-Request-Email`
- `X-User-Email`

The app picks up whichever is present and writes it into every
`GENERATE` log line:

```
2026-04-20 14:22:01  INFO  GENERATE user=shelle.martin@datasite.com company='Acme' type=term2 file=Acme_Datasite_04202026.pptx
```

If none of those headers are forwarded, the `user=` field shows `-` and
everything else still works.

## Logs

All logs go to stdout in the format:

```
YYYY-MM-DD HH:MM:SS  LEVEL  message
```

Key events to watch:

- `Template ... MISSING` at startup → a `.pptx` template file is gone; the
  app will still start but generation for that pricing type will 500.
- `GENERATE user=...` → successful deck generation.
- `BAD_REQUEST user=...` → user submitted invalid form input (e.g. blank
  rate field); the app returned a friendly 400, no action needed.
- `GENERATE failed ...` → real server error, a traceback follows.

Point your standard log sink (Splunk, Datadog, CloudWatch) at stdout.

## Scaling / resource notes

- Memory: ~150 MB per gunicorn worker at steady state.
- CPU: each deck generation pegs one core for 2–5 seconds.
- Disk: none. The app reads the bundled `.pptx` templates at startup and
  streams generated decks straight to the response. **No files are written
  to disk at runtime.**
- Concurrency: `-w 4` comfortably supports ~20 reps. Multiply workers ≈
  concurrent reps / 4.

## Updating pricing templates

The four `.pptx` files next to `app.py` are the templates. To refresh them:

1. Edit the source `.pptx` files.
2. Re-run `python3 compress_templates.py` to regenerate the `_small.pptx`
   versions.
3. If you added rows or moved the industry slides, re-check the slide
   indices in `keep_only_industry_slide()` (currently 6 = Technology,
   7 = Healthcare) and the row indices in `build_deck()`.
4. Re-deploy. No database migration or cache clear needed.

## Known limitations

- Only two industry slides are supported (Technology, Healthcare). Adding
  more industries requires a small code change.
- Clearbit's public logo endpoint may eventually be discontinued; the
  manual-upload fallback will keep the app working if that happens.
- Filename for the downloaded deck is `{Company}_Datasite_{MMDDYYYY}.pptx`;
  two proposals for the same company on the same day produce identical
  filenames. The user's browser will rename (add `(1)` etc.), so this is
  only a cosmetic issue.
