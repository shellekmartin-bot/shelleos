# Datasite Proposal Builder

A small Flask web app that lets a sales rep fill in a form and instantly
download a fully-populated Datasite Diligence proposal deck
(`.pptx`). The rep's name, photo, company, industry, prep period, and
pricing are injected into one of five PowerPoint templates, then the
deck is streamed back to the browser.

**Intended deployment:** internal use only, behind SSO. The app does not
authenticate; the reverse proxy in front of it is expected to.

---

## Table of contents

1. [What it does](#what-it-does)
2. [File layout](#file-layout)
3. [Runtime requirements](#runtime-requirements)
4. [Install & run locally](#install--run-locally)
5. [Exercise the app with sample data](#exercise-the-app-with-sample-data)
6. [Automated test suite](#automated-test-suite)
7. [API reference](#api-reference)
8. [Pricing modes](#pricing-modes)
9. [Adding a new industry](#adding-a-new-industry)
10. [Updating pricing templates](#updating-pricing-templates)
11. [Deployment](#deployment)
12. [Troubleshooting](#troubleshooting)

---

## What it does

- One-page web form at `/`.
- Six pricing modes: single-term flat, two-term flat, Warehouse
  (flat rate billed monthly), single-term tiered, two-terms-with-tiered,
  three-terms-with-tiered.
- Seven industry pills (Technology, Healthcare, Real Estate, Financial,
  Industrials, Energy, Consumer Retail) — selecting one keeps only that
  industry slide in the deck and drops the other six.
- Customisable tier boundaries (defaults: 9,999 / 19,999 pages).
- Rep profile (name, title, headshot) persisted in browser
  `localStorage` so reps don't re-upload every time.
- Celebratory ka-ching + money-rain animation on deck generation.
- `GET /healthz` returns 200 when all five `.pptx` templates load.

---

## File layout

```
pricing-deck-app/
├── app.py                              # Flask app + all deck-build logic
├── index.html                          # The form (single-page UI)
├── requirements.txt                    # Runtime deps (Flask, python-pptx, …)
├── requirements-dev.txt                # Test-only deps (pytest, requests)
├── DEPLOY.md                           # IT/SRE hand-off notes
├── README.md                           # You are here
├── 3_Mos_Pricing_Diligence_small.pptx  # template: 1 flat term
├── Datasite Proposal_6_9_options _small.pptx  # template: 2 flat terms
├── tiered_1term_small.pptx             # template: 1 term, tiered rates
├── tiered_2term_small.pptx             # template: 2 terms, tiered rates
├── tiered_3term_small.pptx             # template: 3 terms, tiered rates
├── compress_templates.py               # dev util — shrink .pptx media
├── create_tiered_templates.py          # dev util — seed tiered templates
├── scripts/                            # one-off migration helpers (empty today)
└── tests/
    └── test_deck_generation.py         # end-to-end smoke suite (see below)
```

---

## Runtime requirements

- **Python 3.9+** (tested on 3.9 and 3.11).
- No database, no disk writes at runtime. Templates are read at startup,
  generated decks are streamed straight to the response.
- Outbound network: none required. (The app used to fetch company logos
  from Clearbit; that has been removed.)

---

## Install & run locally

```bash
cd pricing-deck-app

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt   # optional, only needed to run the tests

python3 app.py
# → http://localhost:5050
```

The server listens on the port in `$PORT` (default `5050`). A health
probe lives at `GET /healthz`.

---

## Exercise the app with sample data

This walkthrough takes ~3 minutes and produces one real `.pptx` you can
open in PowerPoint. Use the **same sample values** that the automated
test uses so behaviour is consistent across manual and CI runs.

### Sample dataset ("Acme Corporation")

| Field            | Value                 |
| ---------------- | --------------------- |
| Your name        | `Shelle Martin`       |
| Your title       | `Sales Director`      |
| Client first names | `Alex`              |
| Company          | `Acme Corporation`    |
| Industry         | `Technology`          |
| Free prep period | `120` days            |
| Cost scenario 1  | `10000` pages         |
| Cost scenario 2  | `20000` pages         |

### Step by step

1. **Start the server.**
   ```bash
   python3 app.py
   ```
   Wait for the line `Datasite Proposal Builder starting on http://localhost:5050`.

2. **Open the form.** In a browser go to http://localhost:5050/.
   - You should see the black "For Datasite Diligence Proposals Only"
     banner at the top.
   - The heading is "Proposal Builder" in Inter / Datasite orange.

3. **Fill in the rep card (top of form).**
   - First & last name: `Shelle Martin`
   - Title: `Sales Director`
   - (Optional) Upload your headshot; the app stores it in the browser
     so you won't have to re-upload next time. Click "Clear saved
     profile" under the photo to wipe it.

4. **Fill in the client section.**
   - Client first names: `Alex`
   - Company: `Acme Corporation`
   - Click the **Technology** industry pill. (Industry is required.)
   - Free preparation period: `120`.
   - Cost scenario 1 pages: `10000`.
   - Cost scenario 2 pages: `20000`.

5. **Pick a pricing mode.** Click one of the six pills. Try each for
   coverage:

   | Pill label                  | Extra fields to fill                                     |
   | --------------------------- | -------------------------------------------------------- |
   | 1 Term                      | months `3`, rate `0.37`                                  |
   | 2 Terms                     | a: months `6`, rate `0.59`  ·  b: months `9`, rate `0.71` |
   | Warehouse                   | months `3`, rate `0.15`                                  |
   | 1 Term Tiered               | rates `0.37 / 0.32 / 0.29`                               |
   | 2 Terms w/Tiered Pricing    | a: `3 mo, 0.40/0.35/0.30`  ·  b: `6 mo, 0.38/0.33/0.28`  |
   | 3 Terms w/Tiered Pricing    | a: `3 mo, 0.59/0.55/0.50` · b: `6 mo, 0.71/0.65/0.60` · c: `9 mo, 0.85/0.78/0.70` |

   For tiered modes, leave the boundary defaults (`9999` / `19999`) or
   override them — both get propagated into the generated deck.

6. **Click Generate.** You should:
   - Hear a "cha-ching" cash-register sound (if your browser isn't muted).
   - See dollar signs and money emoji rise up the screen.
   - Get a download named `AcmeCorporation_Datasite_MMDDYYYY.pptx`.

7. **Open the .pptx in PowerPoint or Keynote.** Verify:
   - Cover slide shows `Acme Corporation` and today's date.
   - Slide 2 (rep card) shows the rep's photo (if uploaded), name, and
     title — name sits on line 1, title on line 2.
   - Exactly one industry slide survives — the Technology slide.
     Healthcare / Real Estate / Financial / Industrials / Energy /
     Consumer Retail slides have been removed.
   - Pricing table (Table 1) shows the rates you entered.
   - Cost estimates table (Table 5) shows computed totals for 10,000
     and 20,000 pages.

If anything above fails, jump to [Troubleshooting](#troubleshooting) or
run the automated suite — it will pinpoint which step broke.

---

## Automated test suite

`tests/test_deck_generation.py` is an end-to-end smoke suite that
**validates system state after data ingestion**: it POSTs the sample
payload above to `/generate` once per pricing mode, opens the returned
`.pptx` with `python-pptx`, and asserts that every piece of submitted
data actually landed in the right slide / cell.

### What's validated

For every pricing mode:

- Server returns HTTP 200 with `Content-Type` = `.pptx`.
- Response body is a non-empty, openable `.pptx`.
- **Rep name**, **company**, and **client first names** appear in the
  deck text (covers cover slide, rep card, and body copy).
- Industry filter correctly drops 6 of the 7 industry slides (total
  slide count ≤ 21, down from the 27-slide source templates).
- Retained industry slide contains the expected industry keyword.
- Pricing rates entered in the form appear in the pricing table
  (`Table 1`) using the `$0.NN/page` format.
- For tiered modes, tier-band labels (`Up to 9,999`, `10,000–19,999`,
  `20,000+`) match the boundaries the caller passed.
- For multi-term tiered modes, the term-header row contains both/all
  the submitted month counts (`3 Months`, `6 Months`, `9 Months`).

Plus two sanity tests:

- `GET /industries` returns the 7 expected industries as JSON.
- A malformed numeric input (`term1_rate=abc`) returns HTTP 400 with
  the "Check your inputs" friendly page — not a 500.

### Run it

The suite **auto-starts the Flask app on :5050** if nothing is listening
there, and tears it down on exit, so you do not have to start the
server manually.

With pytest (recommended for CI):

```bash
pytest tests/ -v
```

Standalone, zero test-framework dependencies:

```bash
python3 tests/test_deck_generation.py
```

Expected output:

```
PASS  test_healthz
PASS  test_industries_endpoint
PASS  test_term1_flat_rate
PASS  test_term2_two_flat_rates
PASS  test_warehouse_mtm
PASS  test_tiered_single_term
PASS  test_tiered_two_terms
PASS  test_tiered_three_terms
PASS  test_custom_tier_boundaries_2tier
PASS  test_bad_input_returns_400

10 passed, 0 failed
```

Runtime: ~2.5 seconds on a dev laptop.

### Pointing the test at a remote server

```bash
PROPOSAL_BUILDER_URL=https://proposal-builder.internal pytest tests/ -v
```

When `PROPOSAL_BUILDER_URL` is set to an already-reachable host, the
test skips the local subprocess boot and just hits the remote URL.

### Adding new tests

Keep the sample fixture (`SAMPLE_REP` / `SAMPLE_DEAL` at the top of
`tests/test_deck_generation.py`) in sync with the README table above.
The two are the single source of truth for "known-good input" across
the project.

---

## API reference

| Route           | Method | Purpose                                                                     |
| --------------- | ------ | --------------------------------------------------------------------------- |
| `/`             | GET    | Serves the single-page form (`index.html`).                                 |
| `/healthz`      | GET    | `{"ok": true}` when all five templates exist. HTTP 500 + list if not.       |
| `/industries`   | GET    | `{"industries": ["Technology", …]}` — drives the UI pills.                  |
| `/generate`     | POST   | Accepts form-encoded fields; streams back a `.pptx` attachment.             |

### `/generate` form fields

| Field           | Required | Notes                                                   |
| --------------- | -------- | ------------------------------------------------------- |
| `pricing_type`  | yes      | `term1` \| `term2` \| `mtm` \| `tiered` \| `tiered2` \| `tiered3` |
| `num_terms`     | yes      | `1` for `term1`/`mtm`/`tiered`; `2` for `term2`/`tiered2`; `3` for `tiered3` |
| `prep`          | yes      | Free preparation period in days                         |
| `pages1`, `pages2` | yes   | Two cost-scenario page counts                           |
| `first_names`   | yes      | Client-side first name(s)                               |
| `company`       | yes      | Client company; appears on cover slide and filename     |
| `industry`      | yes      | Must match a key in `INDUSTRY_SLIDES` (see `app.py`)    |
| `signer_name`, `signer_title` | yes | Rep's own name and title (on rep card)    |
| `rep_photo_b64` | no       | Base64-encoded photo (data URL or bare payload)         |
| `num_tiers`     | tiered modes | `2` or `3`                                          |
| `tier_upper1`, `tier_upper2` | tiered modes | Boundary page counts              |
| `term1_months`, `term1_rate` | term1/mtm/tiered | Single-term flat or tiered   |
| `term2a_*`, `term2b_*` | term2 | Two flat terms                                   |
| `tier1_rate`, `tier2_rate`, `tier3_rate` | tiered | Three band rates           |
| `t2t_a_*`, `t2t_b_*` | tiered2 | Per-term rates for two-term tiered           |
| `t3t_a_*`, `t3t_b_*`, `t3t_c_*` | tiered3 | Per-term rates for three-term tiered |

Missing required fields fall back to documented defaults in `app.py`.
Malformed numerics return HTTP 400.

---

## Pricing modes

| Form pill label              | `pricing_type` | Template                                  |
| ---------------------------- | -------------- | ----------------------------------------- |
| 1 Term                       | `term1`        | `3_Mos_Pricing_Diligence_small.pptx`      |
| 2 Terms                      | `term2`        | `Datasite Proposal_6_9_options _small.pptx` |
| Warehouse                    | `mtm`          | `3_Mos_Pricing_Diligence_small.pptx`      |
| 1 Term Tiered                | `tiered`       | `tiered_1term_small.pptx`                 |
| 2 Terms w/Tiered Pricing     | `tiered2`      | `tiered_2term_small.pptx`                 |
| 3 Terms w/Tiered Pricing     | `tiered3`      | `tiered_3term_small.pptx`                 |

---

## Adding a new industry

1. Open each of the five `.pptx` templates in PowerPoint.
2. Duplicate the **Technology** slide (Cmd-D), rename / restyle it for
   the new industry, and leave it **after** the existing industry
   slides so the existing indices (`6` Technology … `12` Consumer
   Retail) don't shift.
3. Save each template.
4. In `app.py`, add one line to `INDUSTRY_SLIDES`:
   ```python
   INDUSTRY_SLIDES: dict[str, int] = {
       "Technology":      6,
       "Healthcare":      7,
       ...
       "Your New Industry": 13,
   }
   ```
5. Restart the server. The form fetches `/industries` on load and
   renders one pill per entry — no HTML changes required.
6. Update the test assertion `len(data["industries"]) == 7` in
   `tests/test_deck_generation.py` to match the new count.

---

## Updating pricing templates

The five `.pptx` files next to `app.py` are the templates. To refresh:

1. Edit the source files in PowerPoint.
2. Re-run `python3 compress_templates.py` to rebuild the `_small.pptx`
   versions (strips heavy media, keeps layout).
3. If you reordered slides, verify the indices in `INDUSTRY_SLIDES`
   and the row indices in `build_deck()` still match.
4. Re-run `python3 tests/test_deck_generation.py` — any structural
   regression shows up there first.

---

## Deployment

See [`DEPLOY.md`](./DEPLOY.md) for gunicorn config, SSO header
conventions, log format, and scaling guidance. Short version:

```bash
gunicorn -w 4 -b 0.0.0.0:${PORT:-5050} --timeout 60 app:app
```

---

## Troubleshooting

| Symptom                                          | Likely cause / fix                                                              |
| ------------------------------------------------ | ------------------------------------------------------------------------------- |
| `/healthz` returns `{"ok": false, "missing_templates": [...]}` | One of the five `.pptx` files is missing. Copy from the repo. |
| "Check your inputs" 400 page                      | Non-numeric value in a numeric field. Log line starts with `BAD_REQUEST`.       |
| "Something went wrong generating the deck." 500   | Uncaught exception in `build_deck()`. Full traceback is in stdout / the log.    |
| Rep photo won't upload (`413 Request Entity Too Large`) | Should not happen — `MAX_CONTENT_LENGTH` is 25 MB. If it does, bump it in `app.py`. |
| Industry pills missing                            | `/industries` returned an error. Check server log; `INDUSTRY_SLIDES` probably has a syntax error. |
| Tier labels say `Up to 9,999` but rep entered custom bounds | Rep typed the bounds inside the wrong block. Both `blk4` and `blk6` mirror via the `syncTierBounds` JS — inspect the browser console for an error. |
| Tests fail on a fresh clone                       | Make sure `pip install -r requirements.txt -r requirements-dev.txt` ran in the same venv as `python3 app.py`. |
