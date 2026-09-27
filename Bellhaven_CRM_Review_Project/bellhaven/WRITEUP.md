# Assessment writeup

## Objective and status

Keep Bellhaven facility-to-parent relationships accurate without losing billing
history. The deliverable consists of a daily read-only reconciliation pipeline
and a local reviewer application with explicit API write approval.

**Status at packaging: no CRM mutations have been approved or applied.** The live
sandbox was read and a review queue prepared. This is ready for reviewer decisions,
but is not yet the corrected-CRM end state required by the exercise.

## Evidence and findings

The original sandbox contains 121 accounts and 67 contacts. The directory has
three pages with 34 communities; a homepage announcement links to Findlay, the
35th community. The scraper follows directory pagination and homepage/about links,
then reads the detail pages for street, city, state, ZIP, care, administrator, and
phone. The homepage total and directory count are completeness checks, not hardcoded
constants. A mismatch halts the scan rather than inventing missing-location results.
All fetched HTML and CRM data are snapshotted with timestamps and source hashes.

The first reconciliation produced 26 review proposals:

| Classification | Proposals |
| --- | ---: |
| New account | 4 |
| Field/name/parent fix | 12 |
| Duplicate cleanup | 3 |
| Field fix plus duplicate cleanup | 1 |
| CHOW creation plus old-account link | 2 |
| Ambiguous match | 1 |
| No longer listed | 3 |

Twelve website locations need no change. Proposal counts are not API-write counts:
one proposal can contain several operations.

## Matching approach

Rules run over every account, including accounts under other parents; filtering
only by Bellhaven's name would miss renamed/acquired locations. Match candidates
must agree on city and state, plus at least one of street address, name, phone,
or an active contact matching the website administrator. Address normalization
handles punctuation, whitespace, directions, and common street suffixes. ZIP is
supporting evidence, not a hard gate, because it can be wrong in the CRM.

Candidates receive transparent rule scores: geography 20, address 50, exact
normalized name 20, administrator 25, phone 15, ZIP 5, Bellhaven parent 5. These
are ranking weights, not calibrated probabilities. A top-two margin below 15
routes to investigation. A name without a street match additionally needs a
matching administrator or phone to support an address correction. Fuzzy similarity
alone does not produce a write. Each proposal includes all candidates and reasons.

A correctly matched account is compared field by field. Formatting-only street
abbreviations are preserved; official display names, actual address differences,
ZIP, parent and care differences become reviewable corrections. Existing phone
values and contacts are not overwritten. Website care labels map as follows:

| Website | CRM |
| --- | --- |
| Short-Term Rehabilitation & Nursing | Skilled Nursing |
| Memory Support | Memory Care |
| Assisted Living | Assisted Living |

Multiple offerings are represented in the CRM text field using `; `, while the
CSV and source snapshot retain all original labels. The API's OpenAPI document
omits request field schemas and care enums; this text representation has not yet
been verified by an approved live write. Post-write readback will detect rejection
or coercion, and the operation will pause on failure.

## Key judgments for the reviewer

- **Findlay:** include the homepage-only community; attach its account to Bellhaven
  and capture both assisted living and memory support. Revenue exists but AR is
  zero, so direct re-parenting satisfies the SOP.
- **Tiffin and Marietta:** revenue and AR are both positive. Create new Bellhaven
  accounts, then set only `chow_current_account` on the old records. Do not rename,
  inactivate, annotate, change parent, or copy their balances into the new records.
- **Lima and Zanesville:** direct parent correction is permitted because the SOP's
  two simultaneous conditions are not met. Zanesville also needs its current name.
- **Owosso:** the two records share an address; the surviving account has the current
  website administrator. Inactivate the other and set `duplicate_of_account`.
- **Port Clinton, Erie and Monroe:** prefer current Bellhaven identity over older
  same-address records. Losing copies have no financial balances/history. Preserve
  their parent and contacts; mark them Inactive and reference the survivor. No merge
  or delete is attempted. Same address is still a reviewer judgment, not a blanket
  claim that all co-located care businesses are duplicates.
- **Kettering:** three same-address records, no decisive administrator/phone evidence.
  Propose Needs Review on all three rather than invent a survivor. Resolving ownership
  and choosing a survivor remains a manual investigation after this assessment scan.
- **Amberly Manor:** create Hudson, OH. The identically named Colorado account is a
  different facility and remains untouched.
- **Union Square:** the New Albany CRM record is at a different street address with
  different administrator evidence; do not equate it to the website community.
- **Ashtabula:** name, geography, phone and administrator establish identity, but the
  CRM's PO Box may be a legitimate billing address. The proposed replacement with
  the website's physical address is **Medium confidence** and requires the reviewer's
  explicit judgment. Reject if billing and physical addresses must remain distinct.
- **Portsmouth:** the website ZIP is 45662; the CRM has 45626.
- **Alliance, Coldwater and Sandusky:** absent from the complete website. Propose
  Needs Review with evidence, preserving parent and financial fields. A Millstone
  record at Sandusky's address suggests a possible ownership change, but the CRM
  itself is not independent confirmation. Sandusky has $130,000 revenue and $5,200
  AR, so do not move its parent without the protected CHOW process and validated
  destination. No closure or sale is asserted solely from website absence.

## Approval, idempotence and recovery

Only the review app records approvals and applies writes. Every action has a
reviewer name, decision, timestamp, exact payload, source evidence, and audit events.
Rejected proposals remain rejected. IDs use semantic evidence and proposed changes,
not crawl timestamps or incidental HTML changes. Changed business evidence can
produce a new proposal, so a past rejection is not a permanent ban on valid updates.

The app checks the live account against the reviewed snapshot and rechecks the SOP
before a parent write. Each operation is journaled before sending. PATCH outcomes
are checked by reading the account back. Creation includes a unique note marker;
a lost response is recovered by reading accounts for that marker. Uncertain missing
POST outcomes halt for manual investigation rather than creating a second account.
The CHOW link is sent only after a replacement ID exists, and the old record is
verified unchanged apart from that link.

## Tests and limitations

Automated tests use original assessment fixtures and a fake CRM. They exercise
completeness, address normalization, false matches, duplicate survivor selection,
ambiguous/absent records, approval gating, rejection persistence, SOP combinations,
stale records, interrupted CHOW recovery, lost creation-response recovery and
second-run stability. The full simulation applies every proposal to the fake CRM
and produces zero fresh proposals on the second run. **Simulation is not evidence
that the live CRM has been corrected.**

The local UI binds to loopback, escapes untrusted text, validates Host and CSRF,
and keeps the API token out of HTML and saved files. It is a single-reviewer local
app, not a public multiuser service. The durable SQLite file must not be discarded.
There is no remote ETag/conditional-write capability documented in this API, so
preflight checks cannot eliminate the small race with unrelated external CRM edits.
There is no remote atomic transaction for multi-step CHOW. The journal limits
repetition and surfaces partial completion. No automatic remote rollback is used.
The scrape is tailored to the observed detail-page markup; layout drift fails
closed. Counts changing inconsistently between pages require investigation.
An uncertain POST with no recoverable marker and a stale approved proposal require
manual journal reconciliation; no force-apply button is provided.

## AI use and actual time

ChatGPT/Codex inspected the supplied website/API and records, wrote the Python
implementation and review UI, generated the tests, and prepared this writeup.
No LLM calls run in the pipeline, and no paid API keys are needed. The reviewer
must inspect evidence, decide proposals, verify live outcomes and understand the
code well enough to make the requested demo change.

AI-assisted implementation elapsed time is recorded in BUILD_STATUS.json at
packaging. **Human time: fill in your actual hands-on time before submission.**
Do not report the model's elapsed time as your own work time.

## Next steps

1. Review/apply supported changes, reject unsupported ones, re-scan and record the
   actual final CRM state. Resolve Kettering and validate Ashtabula's billing address.
2. Add a reviewer-controlled survivor selection and address-type distinction, with
   a fresh approval payload for every manual override.
3. Add separately verified ownership sources and effective acquisition dates before
   asserting sales/closures. Preserve historical versus current operating entities.
4. Negotiate server-side idempotency keys and conditional updates, then add
   multi-reviewer authentication if the tool moves beyond localhost.
5. Add parser fixtures for layout changes and monitoring/notifications for failed
   daily scans. Do not auto-approve high-scoring matches.
