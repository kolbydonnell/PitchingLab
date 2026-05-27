# Pitching Lab — Session Notes & Resume Guide

> 📌 **Tomorrow's Claude:** read this file first, then keep building from the "Next up" section at the bottom.

---

## What this project is

A backpack-portable pitching analytics platform for baseball/softball coaches and facilities. It ingests CSV exports from three off-the-shelf consumer apps — **Pitch Logic** (ball-flight sensor), **Driveline Pulse** (arm-health wearable), and **ProPlayAI** (3D biomechanics) — fuses them into one canonical per-pitch dataset, heals dropped/missing pitches via timestamp alignment, and generates a Post-Bullpen Report a coach can hand to a player or text to a parent.

**Business goal:** sell as B2B SaaS to high school programs, travel ball teams, and training facilities for ~$99–199/month. Total hardware stack costs ~$610 + ~$30/mo (vs $30k+ for TrackMan). Owner is Kolby (kolbydonnell@gmail.com).

**Owner's situation:** non-technical (knows zero code), needs Claude to write everything. Limited funds — does NOT have the hardware yet, building software first to validate demand before spending.

---

## Key product decisions we made (don't relitigate)

1. **PitchLab AI dropped from the stack.** Originally a 4th app; turns out it doesn't allow CSV export. Pitch Logic already provides modeled movement (Vert_Break_in, Horiz_Break_in) so PitchLab is redundant. Saves $24.99/mo per facility.

2. **The moat is integration + workflow + longitudinal data, NOT algorithms.** All four upstream apps compute their own metrics (spin rate, valgus torque, 3D pose). We're a fusion/reporting/history layer, not a sensor company.

3. **Streamlit for v1 prototype.** Right choice for a non-coder demo; will need to migrate to Next.js + Supabase + Python service when proving model. Not yet.

4. **Ruthless scope cut to v1 (eight features) vs v2 vs v3.** Full feature list is documented in chat history. v1 = ingest, align, store, report, trend, SMS, roster, injury flag. v2 = video clipping, comparison, drill recs, Stripe, multi-tenant. v3 = ML, pose extraction, tunneling, API.

5. **Self-healing aligner = the real differentiator.** Bluetooth drops in field conditions; aligner uses timestamp + velocity-signature matching, injects placeholders, flags healed rows with confidence scores.

---

## What's built and working

**Location on Kolby's Mac:** `~/Desktop/PitchingLab/`

| File | Purpose |
|---|---|
| `pitching_lab.py` | Main Streamlit app — ~1000 lines, single file |
| `start.command` | Double-click launcher |
| `setup.command` | One-time installer (he already ran this) |
| `requirements.txt` | streamlit, pandas, plotly |
| `HOW_TO_RUN.md` | Step-by-step for non-coders |
| `sample_data/` | 11 sample CSVs (Pitch Logic, Pulse, 9 ProPlayAI files) with intentional dropouts for testing |
| `_test_pipeline.py` | End-to-end test of parsing/alignment |
| `_test_demo_mode.py` | Tests for the synthetic data generator |

**Working features in the app:**
- ✅ CSV parsers for Pitch Logic, Pulse, ProPlayAI with **auto-detecting headers** and column-name normalization (handles real-world schema variation)
- ✅ Self-healing timeline aligner with confidence scoring
- ✅ Injury-risk rule engine (valgus stress, AC ratio, early trunk rotation)
- ✅ Action-plan synthesizer with drill recommendations
- ✅ 4-tab UI: Overview, Per-Pitch Detail, Alignment Quality, Action Plan
- ✅ Scatter plot (movement vs velocity, colored by pitch type)
- ✅ Canonical CSV export
- ✅ File-inspector diagnostic that previews raw CSV contents
- ✅ Friendly error messages with column-listing when parsers fail
- ✅ **Demo Mode** — toggle in sidebar; generates a deterministic 12-pitch bullpen for any pitcher name (no CSV uploads needed). Tested and working as of last session.

**Tested with real data:** Kolby uploaded a real Pitch Logic CSV; we hit a KeyError on 'Timestamp', then upgraded the parser to auto-detect and added alias support. He confirmed "it worked."

---

## Outstanding tasks (in order)

Tasks currently in the task list:

- ✅ #1–#8: All complete (CSV samples, main app, parsers, diagnostics, Demo Mode)
- ⏳ #9: **Add PDF export of the Post-Bullpen Report** — use ReportLab. Generates a one-page PDF with KPIs, breakdown, injury flags, action plan. This is the highest-leverage feature for coach pitches — they need something to text a parent.
- ⏳ #10: **Polish UI styling** — better colors, cleaner header, polished empty states. Should look like a real product.
- ⏳ #11: **Update setup + how-to-run docs** — add `reportlab` to requirements.txt, document the Demo Mode + PDF features.
- ⏳ #12: **Verify Phase 1 end-to-end** — run test suite, manually test PDF generation.

After Phase 1, the user wants (in order):

**Phase 2 — multi-player + history:**
- Local SQLite database for session persistence
- Multi-player roster (add/edit/delete)
- Real baselines computed from past sessions (replace the mock `DEMO_BASELINE`)
- Trend charts across sessions
- Cross-session comparison views

**Phase 3 — landing page + sales materials:**
- One-page marketing website (HTML)
- Sell-sheet PDF for emailing facilities
- Demo video script for QuickTime recording
- Google Form / interest capture
- The user has specific high school and travel ball coaches he wants to pitch to

---

## Important context about the user

- He's **not a coder** — writes high-level prompts, expects Claude to write all code
- He's **excellent at product thinking** — he writes detailed pitches, knows the customer, has clear UX vision
- He wants **simple, double-click experiences** — `start.command` and `setup.command` files are how he runs things
- He gets **confused by Terminal paths** — use absolute paths or the drag-and-drop trick when explaining
- His Mac has Python 3.14 installed; pandas + streamlit + plotly installed via pip3
- All files live in `~/Desktop/PitchingLab/`

---

## How to resume tomorrow

1. Kolby will start a new Cowork session.
2. He'll need to point Claude at this file: `~/Desktop/PitchingLab/SESSION_NOTES.md` (or wherever he has the folder).
3. After reading it, jump straight to Task #9 (PDF export) — that's the next priority.
4. Then #10 styling, #11 docs, #12 verify.
5. Then Phase 2 (multi-player + history).

**One-liner Kolby can say to resume:** *"Read SESSION_NOTES.md in my Desktop/PitchingLab folder and continue from Task #9."*

---

## Open product questions to revisit when relevant

- **Product name** — currently "Next-Gen AI Pitching Lab"; placeholder, needs a real brand
- **Pricing** — $99–199/mo tier hypothesized but not validated
- **First customer** — Kolby has specific HS and travel ball coaches in mind; needs polished demo before approaching
- **Hardware financing** — possibility of a facility pre-paying for a pilot in exchange for buying their own gear
- **PitchLab fallback** — if a customer specifically wants camera-traced movement, we'd add manual entry or OCR; not needed for v1
