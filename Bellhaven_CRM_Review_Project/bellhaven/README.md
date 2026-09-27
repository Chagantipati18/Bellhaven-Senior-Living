# Bellhaven CRM ownership reconciliation

A local, standard-library-only Python tool for collecting Bellhaven locations,
matching them to CRM accounts, reviewing evidence, and applying approved changes.

**Current submission status: built and tested; CRM corrections await reviewer approval.**
The included queue is real assessment data, not a mock dashboard. No CRM writes
have been made by this project yet. A queue alone is not a finished assessment.

## Start on your computer

1. Install Python 3.10 or newer if needed (`python --version`). No pip packages,
   paid tools, LLM API, or hosted service are required.
2. Extract this ZIP and open a terminal **inside the `bellhaven` folder**.
3. Run:

   ```sh
   python launch.py
   ```

4. Paste the personal CRM token at the hidden prompt. It is held in memory only.
   Choose **Y** to refresh the website and CRM. This may take a few minutes.
5. Open **http://127.0.0.1:8765** in a browser on that computer.
6. Open each proposal, inspect website and candidate evidence, review the exact
   before/after fields, enter your name, and approve or reject. **Approve applies
   the displayed changes to the real sandbox immediately.**
7. When finished, stop with Ctrl+C and run the launcher again with a fresh scan.
   Confirm approved corrections no longer appear, rejected decisions stay
   rejected, and no errors remain. Keep the `data` folder.

On Windows, `py` can replace `python`. On macOS/Linux, use `python3` if needed.
Do not enter a URL into the terminal as a command; open it in your browser.
The app runs on the computer where you launch it, not on your phone.

## Read-only review, tests, and pipeline

```sh
python app.py                  # Browse included queue without a token
python -m unittest -v          # Tests use a fake CRM; never mutate the sandbox
python pipeline.py --offline  # Reproduce findings from included original HTML/JSON
```

For a live CLI scan, export `BELLHAVEN_API_TOKEN` in your shell and run:

```sh
python pipeline.py
```

`pipeline.py` only makes GET requests. It never applies approved or pending items.
Use `--offline` for reproducing the original snapshot, not refreshing an already
corrected CRM: the snapshot deliberately contains the original messy records.

## Files

- `core.py`: HTML extraction, API client, rules, SQLite ledger, billing safeguards,
  operation journal, and write verification.
- `pipeline.py`: complete read-only scan, snapshots, CSV, and review proposals.
- `app.py`: local HTML review app, CSRF protection, exact changes, audit trail.
- `launch.py`: token prompt and cross-platform launch flow.
- `test_core.py`: scraper, matching, billing and idempotence tests with fake CRM.
- `cron.example`: daily scan schedule; deliberately not installed or activated.
- `.github/workflows/daily-pipeline.yml` (at the repository root): daily GitHub
  Actions schedule with a manual trigger, serialized runs, persisted decision
  state, tests, and downloadable scan results. Add `BELLHAVEN_API_TOKEN` as a
  GitHub Actions repository secret before enabling it.
- `WRITEUP.md`: matching decisions, uncertainty, AI use, next steps, limitations.
- `REVIEW_REPORT.html`: portable evidence and field diffs; **not an approval app**.
- `data/locations.csv`: all 35 scraped locations and care offerings.
- `data/review.sqlite3`: queue, approvals/rejections, execution journal, audit events.
- `data/runs/`: timestamped source HTML, CRM snapshots, proposals and results.
- `data/*.html`, `accounts.json`, `contacts.json`: original read-only test fixtures.

The ZIP includes the current SQLite queue so you can review immediately. The
`.gitignore` excludes mutable state from future Git commits; preserve it locally
or back it up. If sharing source via GitHub, use a private copy of the state or
share the ZIP as allowed by the exercise. Never upload your token.

## Safe daily operation

Use the same persistent `data` directory for scheduled scans and the review app.
Stable semantic proposal IDs prevent unchanged rejected/approved proposals from
being queued again. Pending items superseded by newer evidence are retired.
Each run reports `new_proposal_count` and `suppressed_decided_count`, so a daily
rerun makes this behavior visible instead of counting rediscovered decisions as
new work.
Scraping or pagination failure stops the scan before absence proposals can be
saved. A process lock prevents overlapping local scans and writes. Unfinished
approved operations block new scans until their journal has been resolved.

A CHOW is not a database transaction across the remote API. The journal records
creation and linking separately. Resume recovers a created account from its
unique note marker and does not repeat a POST whose result is uncertain. If the
marker is absent after a timeout, inspect the CRM before repairing the journal;
never delete the database or blindly run the creation again.

For a stale-record error with no remote write, inspect the proposal and journal
and create a fresh review after safely retiring that approval. There is no
one-click force-write or automated rollback: either could bypass review or
overwrite billing history. See WRITEUP.md for remaining limitations.

## Demonstration (45 minutes)

- Explain the business objective and CHOW rule.
- Run a live scan and show 34 directory entries plus homepage-only Findlay.
- Show the matching evidence for Owosso, Ashtabula, and the two Amberly Manors.
- Review Tiffin's two-step CHOW and exact preservation of its old record.
- Review or reject a proposal in the app; show the audit trail and CRM result.
- Run again to demonstrate decision persistence.
- Make a small live code change, such as a displayed filter or an address alias,
  run the relevant tests, and explain its impact.

After corrections, capture the final CRM counts/results and update the submission
status and actual human time in WRITEUP.md before submitting.

