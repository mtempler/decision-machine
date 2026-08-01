# CLAUDE-architecture.md — System Architecture

## Overview

SML-App is a distributed system spanning customer premises and AWS infrastructure. It is designed for privacy, simplicity, and operational independence — customers can prepare and visualize data locally without any cloud dependency, and invoke Scientific Machine Learning processing on demand through a secure, scoped AWS integration. A TSU (Time-Series Unit) billing system controls access to the processing pipeline.

---

## Deployment Zones

```
Customer Premises                         AWS
──────────────────────────────────────    ────────────────────────────────────
SML-App (Flask + background threads)      IAM — SMLAppCLIUser role
  Views, Dashboard, DM viewer               Per-custid scoping via session tags
  Job submission (TSU-gated)               Onboard: add account ID to trust policy
  Job result display                       Offboard: remove account ID
  TSU balance display + request flow
  S3 Download Agent (background thread)   Cognito — Decision Machine User Pool (planned)
  File Watcher (background thread)          One identity per custid
  Warning banner if role assumption fails   Issues scoped temporary credentials

                                          S3 — customer.decision-machine.com
                                            OnDemand/
                                              {descriptor}.csv
                                              {config_|measure_}{descriptor}.ini
                                              TSURequest_{custid}_{timestamp}.txt

                                          S3 — output.customer.decision-machine.com
                                            {custid}/
                                              {custid}_{slug}_{process}_{descriptor}_{info}_TSU_{n}.csv
                                              ERROR_{data_filename}_{timestamp}.txt

                                          DynamoDB — tsu_balances
                                            PK: custid
                                            balance: (atomic counter)

                                          DynamoDB — tsu_transactions
                                            PK: custid
                                            SK: {timestamp}#{uuid}
                                            type: PENDING | PAID | CREDIT | DEBIT
                                            amount, reference, ts, balance

                                          Lambda — OnDemand (SML + DM processing)
                                            Triggered by .ini upload
                                            Processes data, writes output or error
                                            Untouched by TSU system

                                          Lambda — TSU-Request
                                            Triggered by TSURequest_*.txt upload
                                            Creates Stripe customer + invoice
                                            Writes PENDING to tsu_transactions
                                            Deletes request file (PII)

                                          Lambda — TSU-Paid
                                            Lambda Function URL (Stripe webhook)
                                            Verifies Stripe signature
                                            Atomic CREDIT to tsu_balances
                                            PENDING → PAID in tsu_transactions
                                            Writes CREDIT to tsu_transactions

                                          Lambda — TSU-Debit
                                            Triggered by output CSV PUT in S3
                                            Parses TSU count from filename
                                            Atomic DEBIT to tsu_balances
                                            Writes DEBIT to tsu_transactions

                                          Stripe
                                            Invoices for TSU purchases
                                            Webhooks invoice.paid → TSU-Paid
```

---

## Directory Layout (on-prem)

```
SML-App/
  server.py               ← Flask app + background threads
  launcher.py             ← PyInstaller entry point (opens browser, starts Flask)
  sml-app.config          ← per-customer configuration
  sml-app.spec            ← PyInstaller spec
  build.bat               ← single-tester build script
  build-all.bat           ← batch build script (iterates users.csv)
  index.html              ← dashboard
  measurements.html       ← time-series plotter
  job-plot.html           ← SML output chart
  pdfs-table.html         ← DM output viewer
  data/
    views.json            ← view and series metadata
  input/                  ← uploaded time-series CSVs (with headers, uuid-prefixed)
  jobs/                   ← downloaded SML/DM output CSVs (active)
  archive/                ← archived output CSVs
  errors/                 ← downloaded pipeline error files
  downloads/              ← default watch_path (short-lived transit buffer; files deleted from S3 on download)
    errors/               ← error file subfolder within watch_path
```

---

## S3 Buckets

Two separate buckets — input and output are never co-located (AWS anti-pattern).

| Bucket | Role | Who writes | Who reads |
|--------|------|------------|-----------|
| `customer.decision-machine.com` | Input | SML-App | OnDemand Lambda, TSU-Request Lambda |
| `output.customer.decision-machine.com` | Output | OnDemand Lambda | S3 Download Agent, TSU-Debit Lambda |

### Input Bucket Structure

```
s3://customer.decision-machine.com/
  OnDemand/
    {descriptor}.csv                        ← uploaded first, header row stripped
    {config_|measure_}{descriptor}.ini      ← uploaded second — triggers OnDemand Lambda
    TSURequest_{custid}_{timestamp}.txt     ← triggers TSU-Request Lambda; deleted after processing
```

### Output Bucket Structure

```
s3://output.customer.decision-machine.com/
  {custid}/
    {custid}_{slug}_{process}_{descriptor}_{info}_TSU_{n}.csv  ← SML/DM output
    ERROR_{data_filename}_{timestamp}.txt                       ← pipeline error
```

The `_TSU_{n}` suffix in the output filename is the authoritative TSU count set by OnDemand. TSU-Debit parses it directly — no re-calculation needed.

---

## DynamoDB Tables

### tsu_balances

| Attribute | Type | Description |
|-----------|------|-------------|
| `custid` | String (PK) | Customer ID |
| `balance` | Number | Current TSU balance — updated atomically via `ADD` |

One item per custid. Balance is never derived from the log — it is the authoritative atomic counter.

### tsu_transactions

| Attribute | Type | Description |
|-----------|------|-------------|
| `custid` | String (PK) | Customer ID |
| `sk` | String (SK) | `{timestamp}#{uuid}` — sorts newest first with `ScanIndexForward=False` |
| `type` | String | `PENDING`, `PAID`, `CREDIT`, or `DEBIT` |
| `amount` | Number | TSU quantity |
| `reference` | String | Invoice ID (billing) or output filename (debit) |
| `ts` | String | ISO 8601 UTC timestamp |
| `balance` | Number | Balance after this transaction |
| `email` | String | Present on PENDING entries only |

Audit log — not used for balance calculation. Retained for operational visibility.

---

## TSU Billing

### What is a TSU?

One TSU (Time-Series Unit) processes one time-series. The count is computed by the OnDemand Lambda based on the number of symbols and timestamp length, and embedded in the output filename as `_TSU_{n}`. Cost: **$0.10 USD per TSU**.

### TSU Request File

```
TSURequest_{custid}_{timestamp}.txt
```

Plain text, `configparser` format, uploaded to `OnDemand/`:

```ini
[Request]
custid   = tGwuZQqEcx
quantity = 500
email    = customer@example.com

[Billing]
; Additional Stripe invoice fields — to be added
```

Deleted by TSU-Request Lambda immediately after processing — contains PII (email).

### Blocking Policy

| Condition | Behaviour |
|-----------|-----------|
| `tsu_balances` item absent (never funded) | Block submission; show request flow |
| Balance ≤ 0 | Block submission; show request flow |
| Balance < −100 (overdrawn >100) | Block submission; contact support; no request flow |
| Balance > 0 | Allow submission; overdraft permitted up to −100 |

Both client (SML modal) and server (`/submit_sml` HTTP 402) enforce blocking.

### Shared Module

`tsu_tables.py` — shared constants and helpers deployed as a Lambda layer to TSU-Request, TSU-Paid, and TSU-Debit.

---

## Metadata Files

Two `.ini` formats, one per SML process type. The data file is uploaded first (header stripped); the `.ini` is uploaded second and triggers OnDemand Lambda.

### config\_{descriptor}.ini — Binary Process

```ini
[Default]
FileOutput   = output.customer.decision-machine.com/{custid}
Measurements = A,AA,AAMI,AAP,...
Crumbs       = {custid}_{slug}_binary
```

### measure\_{descriptor}.ini — Science-of-Counting (Units) Process

```ini
[Default]
FileOutput   = output.customer.decision-machine.com/{custid}
Measurements = A,AA,AAMI,AAP,...
Crumbs       = {custid}_{slug}_units
```

| Field | Description |
|-------|-------------|
| `FileOutput` | Output bucket path; last segment is custid |
| `Measurements` | Comma-separated symbol list |
| `Crumbs` | Breadcrumb: `{custid}_{slug}_{process}` — prefix of output filename |

---

## Authentication & Access

### CLI Mode (current)

SML-App assumes the `SMLAppCLIUser` IAM role at startup, passing `custid` as a session tag. All S3 and DynamoDB access is scoped per-custid via IAM condition keys.

```python
sts.assume_role(
    RoleArn         = CLI_ROLE_ARN,
    RoleSessionName = f'SMLApp-{CUSTID}',
    ExternalId      = 'decision-machine-cli',
    Tags            = [{'Key': 'custid', 'Value': CUSTID}],
)
```

If role assumption fails at startup, a persistent red warning banner appears in the dashboard. Local features continue to work; all AWS operations are unavailable.

### SMLAppCLIUser Role

**Trust policy** — two statements:
- `sts:AssumeRole` with `ExternalId = decision-machine-cli`
- `sts:TagSession` requiring `custid` in `TransitiveTagKeys`

To onboard a user: add their AWS Account ID to both Principal arrays.
To offboard: remove it. Access revoked immediately.

**Permission policy:**
- `s3:PutObject` on `customer.decision-machine.com/OnDemand/*`
- `s3:ListBucket` on output bucket scoped to `${aws:PrincipalTag/custid}/*`
- `s3:GetObject` on `output.customer.decision-machine.com/${aws:PrincipalTag/custid}/*`
- `s3:DeleteObject` on `output.customer.decision-machine.com/${aws:PrincipalTag/custid}/*`
- `dynamodb:GetItem` on `tsu_balances` scoped to `${aws:PrincipalTag/custid}`

### Cognito Mode (planned)

pycognito exchanges email/password for scoped temporary credentials. Password in system keychain — never on disk.

---

## Components

### 1. SML-App (Flask)

**Implemented:**
- View and series management (create, edit, delete, CSV upload with validation)
- Job submission: Binary and Units processes, `.ini` generation, header-stripped data upload, sequenced S3 upload
- Output CSV header injection at serve time — prepends correct header based on process type
- TSU balance badge in header — reads from DynamoDB via `GET /api/tsu/balance`; click opens request modal
- TSU gating on job submission — blocks if never funded, zero balance, or overdrawn >100
- TSU request flow — inline in SML modal and via header badge modal
- AWS role assumption at startup — warning banner if AssumeRole fails
- Customer ID loaded from `sml-app.config` — read-only badge in header
- S3 Download Agent and File Watcher as background daemon threads
- File management: delete, archive, restore, delete-archived for SML/DM output files
- Error file display with hover tooltip
- Dark/light theme, category filter, search

**Pending:**
- Cognito authentication flow (Mode 2)

---

### 2. Job Submission Flow

1. TSU balance checked via DynamoDB — blocked if never funded, zero, or overdrawn >100
2. User selects Binary or Units; measurements locked from series headers
3. Server generates `.ini` with `Crumbs = {custid}_{slug}_{process}`
4. Uploads data CSV (header stripped) → `OnDemand/{descriptor}.csv`
5. Uploads `.ini` → `OnDemand/{config_|measure_}{descriptor}.ini` (triggers OnDemand Lambda)

---

### 3. TSU-Request Lambda

Triggered by S3 PUT of `TSURequest_*.txt` in `OnDemand/`.

1. Reads `custid`, `quantity`, `email` from request file
2. Finds or creates Stripe customer keyed by `custid` metadata
3. Creates invoice — `quantity × $0.10`, description `N TSUs — OnDemand @ $0.10 each`
4. Finalizes and sends invoice (Stripe emails customer)
5. Writes PENDING to `tsu_transactions`
6. Deletes request file

**Idempotency:** filename used as Stripe idempotency key — prevents duplicate invoices from S3 at-least-once delivery.
**Secrets:** `ondemand/stripe-api-key` from Secrets Manager.

---

### 4. TSU-Paid Lambda

Triggered by Stripe `invoice.paid` webhook via Lambda Function URL (auth: NONE).

1. Verifies Stripe webhook signature
2. Reads `custid` and `quantity` from invoice metadata
3. Atomic `ADD quantity` to `tsu_balances`
4. Updates matching PENDING → PAID in `tsu_transactions`
5. Writes CREDIT to `tsu_transactions`

**Secrets:** `ondemand/stripe-api-key`, `ondemand/stripe-webhook-secret` from Secrets Manager.

---

### 5. TSU-Debit Lambda

Triggered by S3 PUT of `*.csv` in `output.customer.decision-machine.com/{custid}/`.

1. Parses `custid` (first `_`-delimited segment of filename) and `n` from `_TSU_{n}` suffix
2. Atomic `ADD -n` to `tsu_balances`
3. Writes DEBIT to `tsu_transactions`

No CSV reading. No symbol counting. The filename is the authoritative receipt.
OnDemand Lambda is untouched — clean separation of concerns.

---

### 6. S3 Download Agent

Background daemon thread in `server.py`.

- Polls `output.customer.decision-machine.com/{custid}/` every `agent_interval` seconds (default 60)
- Downloads `{custid}_*.csv` → `watch_path/`
- Downloads `ERROR_*.txt` → `watch_path/errors/`
- After each successful download, deletes the file from S3 — this fulfils the security pledge (processing complete = file downloaded) and prevents re-download on subsequent polls
- Delete retries up to 3 times with exponential backoff (2s, 4s, 8s); logs a warning and continues if all retries fail — the file is safely local regardless
- Does not download `fundedTSUs.txt` — balance comes from DynamoDB directly

`watch_path/` is a short-lived transit buffer. Files land there briefly, the file watcher moves them to `jobs/` or `errors/`, and they are gone from both `watch_path/` and S3. Nothing needs to track `watch_path/` state to prevent re-download.

---

### 7. File Watcher

Background daemon thread in `server.py`.

- Polls every `watch_interval` seconds (default 30)
- Copies session CSVs → `jobs/`
- Copies session error files → `errors/`
- Only acts on files in `downloaded_this_session`

**Startup orphan recovery:** pre-populates session set with files in `watch_path` not yet in `jobs/`, `errors/`, or `archive/`.

---

## Data Flow — End to End

### SML Job Flow

```
1. Upload time-series CSV via Views → input/ (headers preserved locally)
2. Review data in measurements.html
3. Click SML → select process → Submit Job (TSU balance checked first)
   → .ini generated
   → data CSV uploaded (header stripped) to OnDemand/{descriptor}.csv
   → .ini uploaded to OnDemand/ (triggers OnDemand Lambda)
4. OnDemand Lambda processes → writes {custid}_..._TSU_{n}.csv to output/{custid}/
5. TSU-Debit Lambda triggers on output CSV PUT
   → parses n from _TSU_{n} suffix
   → atomic debit from tsu_balances
   → DEBIT written to tsu_transactions
6. S3 Download Agent (every 60s) → output CSV → watch_path/
7. File Watcher (every 30s) → jobs/
8. Dashboard refresh → results appear in SML/DM column
```

### TSU Purchase Flow

```
1. Click TSU badge (or submit with zero balance) → request modal
2. Enter quantity → Request TSUs
   → TSURequest_*.txt uploaded to OnDemand/
3. TSU-Request Lambda
   → creates/reuses Stripe customer
   → creates invoice → sends to customer email
   → writes PENDING to tsu_transactions
   → deletes request file
4. Customer pays Stripe invoice
5. Stripe fires invoice.paid → TSU-Paid Lambda URL
   → atomic credit to tsu_balances
   → PENDING → PAID in tsu_transactions
   → CREDIT written to tsu_transactions
6. SML-App polls /api/tsu/balance → DynamoDB GetItem → badge updates
```

---

## File Management

| Action | UI | Filesystem |
|--------|----|------------|
| Delete file | ✕ on file row | Deleted from `jobs/` |
| Archive all | "Archive all" in cell footer | Moved `jobs/` → `archive/` |
| Restore file | ↩ on archived row | Moved `archive/` → `jobs/` |
| Delete archived | ✕ on archived row | Deleted from `archive/` |
| Dismiss error | ✕ on error row | Deleted from `errors/` |

---

## IAM Access Summary

### SMLAppCLIUser Role (assumed by SML-App)

| Resource | Action | Scope |
|----------|--------|-------|
| `customer.decision-machine.com/OnDemand/*` | `s3:PutObject` | All |
| `output.customer.decision-machine.com` | `s3:ListBucket` | `{custid}/*` via session tag |
| `output.customer.decision-machine.com/{custid}/*` | `s3:GetObject` | Per session tag |
| `output.customer.decision-machine.com/{custid}/*` | `s3:DeleteObject` | Per session tag |
| `tsu_balances` | `dynamodb:GetItem` | Per session tag custid |

### Lambda Roles

| Lambda | Resource | Actions |
|--------|----------|---------|
| OnDemand | `customer.decision-machine.com/OnDemand/*` | `GetObject`, `DeleteObject` |
| OnDemand | `output.customer.decision-machine.com/*` | `PutObject` |
| TSU-Request | `customer.decision-machine.com/OnDemand/*` | `GetObject`, `DeleteObject` |
| TSU-Request | `tsu_transactions` | `PutItem` |
| TSU-Request | Secrets Manager `ondemand/stripe-api-key` | `GetSecretValue` |
| TSU-Paid | `tsu_balances` | `UpdateItem` |
| TSU-Paid | `tsu_transactions` | `PutItem`, `UpdateItem`, `Query` |
| TSU-Paid | Secrets Manager `ondemand/stripe-api-key`, `ondemand/stripe-webhook-secret` | `GetSecretValue` |
| TSU-Debit | `tsu_balances` | `UpdateItem` |
| TSU-Debit | `tsu_transactions` | `PutItem` |

---

## Lambda Layers

| Lambda | Layer contents |
|--------|---------------|
| TSU-Request | `stripe` Python package + `tsu_tables.py` |
| TSU-Paid | `stripe` Python package + `tsu_tables.py` |
| TSU-Debit | `tsu_tables.py` only |
| OnDemand | **unchanged** |

---

## Configuration File

`sml-app.config` — personalised per customer, baked into each PyInstaller build:

```ini
[identity]
custid            = tGwuZQqEcx
auth_mode         = cli                              ; cli | cognito
email             = customer@example.com
cli_role_arn      = arn:aws:iam::741600857758:role/SMLAppCLIUser
cognito_username  = customer@example.com             ; Cognito mode only
cognito_region    = us-east-1                        ; Cognito mode only
user_pool_id      = us-east-1_XXXXXXXXX              ; Cognito mode only
client_id         = XXXXXXXXXXXXXXXXXXXXXXXXXX       ; Cognito mode only
identity_pool_id  = us-east-1:XXXXXXXX-XXXX-...     ; Cognito mode only

[storage]
input_bucket   = customer.decision-machine.com
output_bucket  = output.customer.decision-machine.com
watch_path     = downloads
watch_interval = 30
agent_interval = 60

[server]
port = 5000
```

---

## Deployment Packaging

```bat
build.bat {custid} {email}          ← single tester package
build-all.bat users.csv             ← batch build from custid,email CSV
```

Each build writes a personalised `sml-app.config`, runs PyInstaller, produces `dist\SML-App-{custid}.zip`. HTML files overwritten on every launch; `sml-app.config` preserved after first launch.

Registration page (`register.html`) — two-tab form (CLI / Cognito). Async submit triggers GitHub Actions build workflow, uploads ZIP to `downloads.decision-machine.com/{custid}/`, sends pre-signed download link via SES.

---

## Privacy Model Summary

| Data | Where it lives | Retention |
|------|----------------|-----------|
| Raw time-series CSVs | `input/` on customer machine | Until deleted by user |
| SML input (during processing) | `OnDemand/` | Deleted by Lambda after processing |
| SML/DM output | Output bucket + local `jobs/` or `archive/` | Until deleted by user |
| Pipeline errors | Output bucket + local `errors/` | Until dismissed |
| TSU request file | `OnDemand/` | Deleted immediately after processing |
| TSU transaction log | `tsu_transactions` DynamoDB | Operational retention |
| Customer email | `sml-app.config` + TSU request file (briefly) | Request file deleted after processing |
| Stripe API key | Secrets Manager | Lambda execution only |
| AWS role credentials | Memory only (assumed on startup) | Session lifetime |

---

## CLAUDE Doc Ownership

| Component | CLAUDE doc |
|-----------|------------|
| `server.py`, `index.html` | `CLAUDE-core.md` |
| `measurements.html` | `CLAUDE-views.md` |
| `job-plot.html` | `CLAUDE-sml.md` |
| `pdfs-table.html` | `CLAUDE-dm.md` |
| `launcher.py`, `build.bat`, `build-all.bat`, `sml-app.spec` | `CLAUDE-core.md` |
| `lambda_tsu_request.py`, `lambda_tsu_paid.py`, `lambda_tsu_debit.py`, `tsu_tables.py` | `CLAUDE-core.md` |
| `iam-cli-user-role.json` | `CLAUDE-core.md` |
| `register.html` | `CLAUDE-core.md` |
| Cognito auth flow (planned) | `CLAUDE-core.md` |

---

## Known Limitations and Future Work

- Cognito authentication (Mode 2) not yet implemented
- Registration page backend (GitHub Actions trigger, SES email) not yet built
- No job status tracking (pending/in-progress) — files appear after Lambda completes
- No retry logic on failed S3 uploads
- Deployment service registration (Windows Task Scheduler) not yet automated
- macOS support not yet built
