# AKAV Job-Workbook Import Pipeline

Scrapes per-show `.xlsx` workbooks (crew, positions, day rates, letter
grades) into the master AKAV Google Sheet — the same sheet the onboarding
website writes to. The master stays **one row per person**; every imported
show appends a 5-column group to the right:

```
<Show Label> — Position(s) | Days | Rate | Total | Grade
```

plus a persistent **Notes** column (col 22, merge-only — never clobbered).
Raw per-person-per-day rows land in a hidden **WorkRecords** tab (the source
of truth the summary columns are recomputed from), a **Shows** tab registers
each imported show, and **ImportLog** records every chunk received.

## One-time setup

1. **Apps Script**: open the master Sheet → Extensions → Apps Script, replace
   the code with `../AKAV Onboarding/google-apps-script.gs`.
   - The sheet is full of test rows from building the form. In the Apps
     Script editor, pick **`initFreshDatabase`** in the function dropdown and
     press **Run** (once). It renames all current data tabs to
     `OLD <name> <date>` and hides them, then creates clean, formatted
     `Submissions` / `Incomplete Submissions` tabs. Same spreadsheet, same
     URL — the website keeps working. When you no longer need the old test
     tabs, run `deleteArchivedTabs()`.
2. **Token**: in the Apps Script editor → Project Settings → Script
   Properties → add `IMPORT_TOKEN` = a long random string. Import/roster
   routes refuse to run until it's set. The onboarding form needs no token.
3. **Deploy**: Deploy → **Manage deployments → edit → New version** on the
   EXISTING deployment. (A brand-new deployment gets a new `/exec` URL and
   would orphan the endpoint hardcoded in `index.html`.)
4. Locally:
   ```
   export AKAV_ENDPOINT='https://script.google.com/macros/s/…/exec'
   export AKAV_TOKEN='the same secret'
   pip install -r requirements.txt        # just openpyxl
   ```

## Importing a workbook

```bash
cd pipeline

# 1. Parse → writes out/<show-slug>-batch.json + prints a preview
python3 -m akav_import parse "../Example Documents/VegasCT13.xlsx"

# useful flags:
#   --show-label "Cisco Live 2026"    override detected label (also the column-group name!)
#   --grades grades.csv               manual grades: name_or_email,grade per line
#   --exclude "AK Travel"             drop a pseudo-person row (repeatable)

# 2. Preview against the live roster (shows EMAIL/PHONE/NAME?/NEW match per person)
python3 -m akav_import preview out/cisco-live-2026-batch.json

# 3. Upload (asks for typed 'yes'; --yes to skip). Writes out/<slug>-receipt.json
python3 -m akav_import upload out/cisco-live-2026-batch.json
```

**Read the preview before uploading.** Loud things to check:

- `GRADE COLUMNS DETECTED` — confirm the unlabeled column really is grades.
- `AMBIGUOUS NAME` — two humans share a normalized name; their name-only
  rows were NOT merged. Fix the workbook or accept the split.
- `NAME? -> row N` matches — person matched by name alone (no email/phone
  overlap). Verify row N is really them before uploading.
- `appears in Crew Status but has no parsed work records` — real for
  partial workbooks; junk for rows like "AK Travel" (use `--exclude`).

Re-running an upload is safe: records dedupe by `recordHash`
(person+date+position+call start), and the summary columns are recomputed,
not appended. `ImportLog` will show `skippedDupes` = everything.

## How matching works

Person identity = normalized **email**, then **phone digits**, then
**name** (lowercased, accents stripped). The same logic runs in
`akav_import/normalize.py` and in `normEmail/normPhone/normName` in the
Apps Script — **if you change one, change the other** (there's a parity
test idea in the repo history: same inputs through both must be identical).

When someone a job import created later onboards through the website, the
onboarding handler now finds their row (email → phone → name) and fills
columns 1–21 in place, preserving Notes and all show columns.

## Testing (never against the live sheet)

1. Drive → right-click the master sheet → **Make a copy** (this copies the
   bound Apps Script too).
2. In the copy's Apps Script editor: paste the current `.gs`, set a test
   `IMPORT_TOKEN`, Deploy → New deployment (Execute as me / Anyone) → use
   that `/exec` URL as `AKAV_ENDPOINT`.
3. Matrix worth re-running after script changes:
   - import each of the three example workbooks
   - re-upload one batch → ImportLog all-skipped, summaries unchanged
   - a second show for the same people → second column group, first intact
   - onboarding regression: POST a synthetic onboarding payload before and
     after; then import-then-onboard and onboard-then-import.

## Files

```
akav_import/
  workbook.py     sheet classification + column-role inference (the fragile heart)
  parse_tidy.py   'Scratch Paper' / 'Show details' sheets (best source: has rates)
  parse_wb.py     'Workbook' / 'WB - LIVE' sheets + show metadata (rows 1-5)
  parse_status.py 'Crew Status' sheets ($ due, notes)
  grades.py       unlabeled letter-grade column detection (A-F/X, +/-) + CSV side input
  identity.py     union-find person merging; personKey + recordHash
  batch.py        orchestrates one workbook → batch JSON
  preview.py      terminal tables + roster matching
  uploader.py     chunked POST (urllib, 150 records/chunk, retries 2/8/30s)
  cli.py          parse | preview | upload
```

Notes on data semantics:

- **Total** prefers Crew Status `$ due` (actual paid, includes OT
  adjustments) over the sum of day rates; the cell note on the sheet says
  which one it is.
- Tidy sheets can cover only part of a show (AnaheimPRG's Scratch Paper is
  BO-only), so Workbook-sheet records are unioned in and deduped.
- Grade `X` means do-not-rehire. Manual grades CSV wins over detected ones.
