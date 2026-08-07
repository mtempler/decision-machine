# CLAUDE-architecture.md — System Architecture

## Overview

SML-App is a distributed system spanning customer premises and AWS infrastructure. It is designed for privacy, simplicity, and operational independence — customers can prepare and visualize data locally without any cloud dependency, and invoke Scientific Machine Learning processing on demand through a secure, scoped AWS integration. A TSU (Time-Series Unit) billing system controls access to the processing pipeline.

---

## Deployment Zones

```
Customer Premises                         AWS
──────────────────────────────────────    ────────────────────────────────────
SML-App (Flask + background threads)      Cognito — Decision Machine User Pool
  Views, Dashboard, DM viewer                One user per custid (custom:custid attribute)
  Job submission (TSU-gated)                 pycognito SRP auth; password in OS keychain
  Job result display                         (never written to disk)
  TSU balance display + request flow
  S3 Download Agent (background thread)    Cognito Identity Pool
  File Watcher (background thread)           ABAC: custid claim → PrincipalTag
  Persistent auth banner (polled 60s)        Issues scoped temporary AWS credentials
  if Cognito authentication fails
                                            IAM — SMLAppCognitoUser role (shared)
                                              Per-custid scoping via PrincipalTag condition

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
                                            Triggered by config_/measure_/pdfs_ .ini upload
                                            Processes data, writes output or error
                                            Untouched by TSU system

                                          EventBridge — default event bus
                                            Rule: S3 Object Created on OnDemand/var_*
                                            Target: OnDemand_VaR State Machine
                                            Input transformation: S3 Records format

                                          Step Functions — OnDemand_VaR State Machine
                                            Triggered by EventBridge on var_ .ini upload
                                            Orchestrates VaR + EWS workflow steps
                                            Enables retry, branching, future expansion
                                            IAM role: OnDemand_VaR-role / dm_var_policy

                                          Lambda — VaR Calculation (step in OnDemand_VaR)
                                            Computes VaR, EWS assessment
                                            Writes output CSV + .meta companion file
                                            Layer: Klayers scipy (Python 3.12)

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
  setup.html              ← first-launch wizard (custid/email/password/region)
  cognito-regions.json    ← region → {user_pool_id, client_id, identity_pool_id} map;
                             refreshed every launch like the HTML, not preserved like
                             sml-app.config — see Authentication & Access
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
    {config_|measure_|pdfs_}{descriptor}.ini ← uploaded second — triggers OnDemand Lambda
    var_{descriptor}.ini                    ← uploaded second — triggers EventBridge → OnDemand_VaR State Machine
    TSURequest_{custid}_{timestamp}.txt     ← triggers TSU-Request Lambda; deleted after processing
```

### Output Bucket Structure

```
s3://output.customer.decision-machine.com/
  {custid}/
    {custid}_{slug}_{process}_{descriptor}_{info}_TSU_{n}.csv  ← SML/DM output
    {custid}_{slug}_{process}_{descriptor}_{info}_TSU_{n}.meta  ← companion metadata (EWS)
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

All `.ini` formats share the same three infrastructure lines in `[Default]`. Lines below those three are contractual values specific to the workflow — they are immutable once deployed. A new contract value requires a new workflow prefix (and a new EventBridge rule and State Machine). The data file is uploaded first (header stripped); the `.ini` is uploaded second and triggers processing.

### Shared infrastructure lines (all formats)

```ini
[Default]
FileOutput   = output.customer.decision-machine.com/{custid}
Measurements = A,AA,AAMI,AAP,...
Crumbs       = {custid}_{slug}_{process}
```

| Field | Description |
|-------|-------------|
| `FileOutput` | Output bucket path; last segment is custid |
| `Measurements` | Comma-separated symbol list |
| `Crumbs` | Breadcrumb: `{custid}_{slug}_{process}` — prefix of output filename |

### config\_{descriptor}.ini — Binary Process

Triggers: OnDemand Lambda (S3 PUT trigger). No contractual values beyond the three shared lines.

```ini
[Default]
FileOutput   = output.customer.decision-machine.com/{custid}
Measurements = A,AA,AAMI,AAP,...
Crumbs       = {custid}_{slug}_binary
```

### measure\_{descriptor}.ini — Units Process

Triggers: OnDemand Lambda (S3 PUT trigger). No contractual values beyond the three shared lines.

```ini
[Default]
FileOutput   = output.customer.decision-machine.com/{custid}
Measurements = A,AA,AAMI,AAP,...
Crumbs       = {custid}_{slug}_units
```

### pdfs\_{descriptor}.ini — Decision Machine (Probability Distribution) Process

Triggers: OnDemand Lambda (S3 PUT trigger). Adds `ConfidenceLevel` as a contractual value — required, no default, decimal format. Used to compute VaR: `P(L > VaR) < 1 - c`.

```ini
[Default]
FileOutput        = output.customer.decision-machine.com/{custid}
Measurements      = A,AA,AAMI,AAP,...
Crumbs            = {custid}_{slug}_pdfs
ConfidenceLevel   = 0.95
```

### var\_{descriptor}.ini — VaR Workflow Process

Triggers: EventBridge → `OnDemand_VaR` State Machine (S3 PUT trigger on `OnDemand/var_` prefix). Adds `ConfidenceLevel` as a contractual value — required, no default, decimal format.

```ini
[Default]
FileOutput        = output.customer.decision-machine.com/{custid}
Measurements      = A,AA,AAMI,AAP,...
Crumbs            = {custid}_{slug}_var
ConfidenceLevel   = 0.95
```

## Companion Metadata Files (.meta)

Output files from the `var_` workflow include a companion `.meta` file alongside the CSV. The `.meta` file carries EWS overlay data consumed by `dm-plot.html`. It is downloaded by the S3 agent and served by `/api/jobfiles/<filename>/meta`.

### Format

Plain text key=value pairs, one per line:

```
expected_demand_0 = 10.52    ← most recent (dashed vertical line position)
total_demand_0    = 10.05    ← most recent (fully opaque green dot)
expected_demand_1 = 10.81
total_demand_1    = 10.50
expected_demand_2 = 11.23
total_demand_2    = 11.35
expected_demand_3 = 11.45
total_demand_3    = 12.03
expected_demand_4 = 11.02
total_demand_4    = 11.85    ← oldest (most transparent green dot)
```

- Index 0 is most recent; index 4 is oldest
- `expected_demand_0` truncated (`Math.floor`) gives the dashed line unit
- `total_demand_n` truncated gives the dot unit for each trail position
- Five dots rendered back-to-front; opacities: 0.10, 0.22, 0.40, 0.65, 1.00
- `.meta` absent → plot renders normally without overlay

---

**Contract immutability:** Once a workflow prefix is deployed, its contract (the set of keys below the three shared lines) is frozen. Adding a new contractual key requires a new prefix (e.g. `var2_`) with its own EventBridge rule and State Machine.

---

## Authentication & Access

### Design history — Managed Login considered, declined

Amazon Cognito's Managed Login (formerly Hosted UI) was evaluated as an alternative to the SRP flow below — it would remove the custom SRP code and the `NEW_PASSWORD_REQUIRED` challenge-response entirely, since Cognito's own hosted page handles `FORCE_CHANGE_PASSWORD` natively. It was **declined**, for two reasons:

1. It's an OAuth redirect flow — the browser goes to a Cognito-hosted domain and the app gets back an authorization code, never a password. That's incompatible with the current keychain-cached-password model; adopting it means replacing the credential mechanism outright, not extending it.
2. It doesn't actually solve the thing that looked like the gap: Managed Login has no dedicated "change my password while logged in" page — only "Forgot Password" (reset via a code). So the self-service ask isn't better served there than by the reset flow already partially built here.

pycognito/SRP remains the chosen mechanism. This section describes that path.

### Provisioning handoff (SML-Training → SML-App)

SML-Training, not SML-App, owns customer provisioning. By the time a customer downloads and launches SML-App, the following is already true and baked into that build's `sml-app.config`:

- `custid`, `email`, `cognito_username` (= email)
- `cognito_region`, `user_pool_id`, `client_id`, `identity_pool_id` — resolved for that customer's region already; SML-App does not ask the customer to pick a region or enter these IDs
- A Cognito user already exists with a **permanent** password the customer chose during the SML-Training flow — provisioned via `AdminCreateUser` (`MessageAction='SUPPRESS'`, no Cognito-sent email) immediately followed by `AdminSetUserPassword(Permanent=True)` using that password. No temporary password is ever generated or emailed.

Consequence: **`NEW_PASSWORD_REQUIRED` does not occur at onboarding.** First launch of SML-App is a plain SRP login with a password the customer already knows. The only remaining case for that challenge is an admin-triggered mass password reset on an *existing* account (e.g. after a security incident) — see below.

`cognito-regions.json`, `GET /api/regions`, and the region-resolution logic in `/api/setup` (documented as "Region Selection" in an earlier draft of this doc) belong to SML-Training's provisioning tooling, not SML-App, under this model — they exist in this repo currently as a holdover from before the handoff was designed, and are slated for removal once SML-Training owns that resolution. `setup.html` itself is likely to shrink to nothing beyond a one-time password prompt (to seed the local keychain) or disappear if that's instead handled by whatever triggers the first AWS action — see Known Limitations.

### Cognito Auth (current — sole auth path)

SML-App authenticates against a shared Cognito User Pool and exchanges the resulting ID token for scoped temporary AWS credentials via a Cognito Identity Pool. There is no CLI/IAM-role mode in the app — every customer install uses Cognito.

Auth is triggered only by the customer actually invoking an AWS-dependent action — Submit Job, opening the TSU modal, or requesting TSUs — never by a background poll or at app startup. `get_boto_session()` is the single chokepoint all three call. The first call triggers authentication; subsequent calls reuse the cached session until the credentials are within 5 minutes of expiring, at which point it transparently re-authenticates on the next action.

```
1. get_boto_session() called (customer clicks Submit Job / TSU balance / TSU request)
2. Cached credentials valid?  → yes → reuse
                              → no  → refresh_token renewal (pycognito)
                                        succeeds → new ID token
                                        fails    → full SRP login:
                                                     password read from OS keychain
                                                     (keyring, keyed on cognito_username)
3. ID token exchanged with Identity Pool:
     cognito-identity:GetId
     cognito-identity:GetCredentialsForIdentity
4. Scoped temporary AWS credentials cached with expiry
```

**Password storage:** written to the OS keychain (via `keyring`) the first time the customer authenticates — never written to `sml-app.config` or any file on disk. (Historically this was described as being written by a setup-wizard `/api/setup` password field; that field's fate is tied to the provisioning handoff above — see Known Limitations for the current gap.)

**Forced password changes (narrower scope than originally built):** Cognito returns `NEW_PASSWORD_REQUIRED` instead of tokens when a user is in `FORCE_CHANGE_PASSWORD` state. Under the provisioning handoff above, this **cannot happen at onboarding** — it only occurs if an admin later forces a reset on an existing account (e.g. an AWS-side security incident requiring everyone to set a new password). This is why the CLI-mode design never needed anything like it: an IAM role assumed via trust policy has no human credential to expire, rotate, or force-reset in the first place. Password lifecycle management is a cost specific to choosing a human-authenticated identity provider (Cognito) over machine trust (CLI's `sts:AssumeRole`) — narrowed now to the one case that can actually still occur, rather than also covering onboarding.

Handling: detected in `_cognito_id_token()` and surfaced via `GET /api/auth/status` (`password_change_required: true`) and via the same flag on `/api/tsu/balance`, `/api/tsu/request`, and `/api/submit-sml` responses — whichever action the customer was attempting when it happened. The dashboard shows an inline "set new password" form in the auth banner, triggered only by that action's response, never by a background check. `POST /api/auth/change-password` completes the Cognito challenge using the still-valid old password from the keychain (the customer only supplies the new one), then overwrites the keychain entry and forces a clean re-authentication.

Requires `USER_PASSWORD_AUTH` enabled as an allowed auth flow on the Cognito App Client, alongside the `ALLOW_USER_SRP_AUTH` flow `pycognito` uses for normal logins — the challenge-response step uses `USER_PASSWORD_AUTH` directly via boto3 rather than pycognito's SRP helper, since it needs a `Session` token under direct control rather than one buried inside pycognito's internals.

Cognito username is always the account email — there is no separate "Cognito username" field anywhere in SML-App; `cognito_username` in `sml-app.config` is just email, copied.

**Known gap — MFA:** an MFA challenge instead of `NEW_PASSWORD_REQUIRED` is not handled and will still surface as a generic auth failure with no path to resolution from the dashboard. See Known Limitations.

### Per-custid Scoping (ABAC)

Mirrors the isolation guarantee the old CLI-mode session-tag design provided, using Cognito's equivalent mechanism instead of `sts:TagSession`:

1. **Custom attribute** `custid` on the User Pool, set once per customer at account creation (now: by SML-Training's provisioning step). Custom attributes ride in the ID token automatically — no pre-token-generation Lambda required.
2. **Attributes for access control**, enabled on the Identity Pool, mapping the `custid` claim to a principal tag on the assumed role's session — this is enforced by Cognito itself; a customer's own client cannot influence which tag value gets attached.
3. **One shared IAM role**, `SMLAppCognitoUser`, attached to the Identity Pool's authenticated role. Trust policy allows `sts:AssumeRoleWithWebIdentity` + `sts:TagSession` conditioned on the identity pool ID. Permission policy is the same shape as the retired `SMLAppCLIUser` policy, just keyed off `${aws:PrincipalTag/custid}` instead of the session tag.

**Status:** this ABAC configuration has not yet been applied in the Cognito/IAM console — see Known Limitations. Until it is, a Cognito identity's federated credentials are not actually restricted to its own `custid`'s data.

The `SMLAppCognitoUser` permission policy (see IAM Access Summary below) is a straight port of the retired `SMLAppCLIUser` policy — same resource scoping, condition key swapped from session tag to principal tag.

Onboarding a new customer: SML-Training creates their Cognito user with `custid` set. Offboarding: disable the Cognito user — access revoked immediately, no IAM trust-policy edit needed (unlike the old per-AWS-account CLI onboarding flow).

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
- Cognito authentication — SRP login, refresh_token renewal, password in OS keychain
- Persistent auth-status banner — polled every 60s via `GET /api/auth/status`, not just at startup
- Forced Cognito password change handling — inline dashboard form, no restart needed
- Customer ID loaded from `sml-app.config` — read-only badge in header
- S3 Download Agent and File Watcher as background daemon threads
- File management: delete, archive, restore, delete-archived for SML/DM output files
- Error file display with hover tooltip
- Dark/light theme, category filter, search

**Pending:**
- Cognito Identity Pool ABAC scoping (per-custid isolation) — not yet applied in the AWS console
- MFA challenge handling — surfaces as a generic auth failure today

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

### SMLAppCognitoUser Role (assumed via Cognito Identity Pool, shared across all customers)

| Resource | Action | Scope |
|----------|--------|-------|
| `customer.decision-machine.com/OnDemand/*` | `s3:PutObject` | All |
| `output.customer.decision-machine.com` | `s3:ListBucket` | `{custid}/*` via principal tag |
| `output.customer.decision-machine.com/{custid}/*` | `s3:GetObject` | Per principal tag |
| `output.customer.decision-machine.com/{custid}/*` | `s3:DeleteObject` | Per principal tag |
| `tsu_balances` | `dynamodb:GetItem` | Per principal tag custid |

*Principal tag (`custid`) is attached automatically by the Identity Pool via Attributes for Access Control, sourced from the `custid` custom attribute on the authenticating Cognito user — see Authentication & Access. Not yet configured; see Known Limitations.*

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
| OnDemand_VaR | `customer.decision-machine.com/OnDemand/*` | `GetObject`, `PutObject`, `DeleteObject` |
| OnDemand_VaR | `output.customer.decision-machine.com/*` | `PutObject` |

---

## Lambda Layers

| Lambda | Layer contents |
|--------|---------------|
| TSU-Request | `stripe` Python package + `tsu_tables.py` |
| TSU-Paid | `stripe` Python package + `tsu_tables.py` |
| TSU-Debit | `tsu_tables.py` only |
| OnDemand | **unchanged** |
| OnDemand_VaR | Klayers scipy (Python 3.12) — includes numpy |

---

## Configuration File

`sml-app.config` — personalised per customer, written by the setup wizard on first launch. Password is never stored here — see [Authentication & Access](#authentication--access). `user_pool_id`, `client_id`, and `identity_pool_id` are resolved server-side from `cognito-regions.json` based on the region the customer selects — the customer never sees or enters these directly.

```ini
[identity]
custid            = tGwuZQqEcx
email             = customer@example.com
cognito_username  = customer@example.com
cognito_region    = us-east-1
user_pool_id      = us-east-1_XXXXXXXXX
client_id         = XXXXXXXXXXXXXXXXXXXXXXXXXX
identity_pool_id  = us-east-1:XXXXXXXX-XXXX-...

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

**Superseded by the SML-Training handoff** (see Authentication & Access → Provisioning handoff): `build.bat`/`build-all.bat` need to write the full Cognito field set (`cognito_username`, `cognito_region`, `user_pool_id`, `client_id`, `identity_pool_id`), not just `custid`/`email`, since SML-Training resolves those before triggering a build rather than SML-App resolving them at runtime. The earlier plan described here — a standalone `register.html` collecting custid/email and triggering a GitHub Actions build — is superseded by SML-Training owning that collection and provisioning step instead; this doc doesn't track SML-Training's implementation, only the contract it must hand off (see Provisioning handoff).

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
| Cognito password | OS keychain (`keyring`) | Until customer/setup change |
| Federated AWS credentials | Memory only (obtained lazily, refreshed before expiry) | Until process exit or refresh |

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
| `iam-cognito-user-role.json` | `CLAUDE-core.md` | not yet created — role/ABAC config to be applied in AWS console; see Known Limitations |
| `register.html` | `CLAUDE-core.md` | superseded — provisioning now belongs to SML-Training, not this repo; see Authentication & Access |
| Cognito auth flow | `CLAUDE-core.md` |

---

## Known Limitations and Future Work

See `BACKLOG.md` at the repo root for the full prioritised list. Summary:

- Cognito Identity Pool ABAC scoping not yet applied — per-custid isolation for Cognito credentials is designed (see Authentication & Access) but not yet configured in AWS
- `NEW_PASSWORD_REQUIRED` handling exists but is now narrower in scope than originally built — only the admin-forced-reset case applies once SML-Training owns onboarding (see Provisioning handoff); MFA challenge responses from Cognito still surface as a generic auth failure, with no in-app path to resolve them
- **First-launch password entry is unresolved.** SML-Training provisions the Cognito user and its password, but SML-App's OS keychain is per-machine and can't be pre-populated by a download — some interactive moment for the customer to type their (already-chosen) password once, seeding the keychain, is still needed. Whether that's a shrunk `setup.html`, an inline prompt folded into `index.html` the first time `get_boto_session()` finds no keychain entry, or something else, is undecided.
- `cognito-regions.json`, `GET /api/regions`, and the region-resolution branch of `/api/setup` are a holdover in this repo from before the SML-Training provisioning handoff was designed — they belong in SML-Training's provisioning tooling, not here, and should be removed from SML-App once that handoff is implemented there
- Registration/provisioning (SML-Training: `AdminCreateUser` + `AdminSetUserPassword`, custid/region/pool-ID baking into the build) lives in a separate codebase from SML-App and is not covered by this document
- No job status tracking (pending/in-progress) — files appear after Lambda completes
- No retry logic on failed S3 uploads
- Deployment service registration (Windows Task Scheduler) not yet automated
- macOS support not yet built
