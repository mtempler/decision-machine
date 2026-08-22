# Decision Machine — Onboarding Architecture

Version 2.0 · August 2026
Supersedes Version 1.0 (March 2026) — that version described the pipeline as
CLI-account-based (`auth_mode = cli`, no Cognito). It is now Cognito-based
throughout. `register.html` itself is still the frontend — it was not
retired — but the backend Lambda(s) it calls have changed.

> **Confidence note:** Sections describing `server.py`, `build.bat`,
> `sml-app.spec`, `setup.html`, and the Cognito/keyring auth flow are drawn
> directly from the current source and are accurate as of this version.
> Sections describing the **accept/fulfill Lambda pipeline** behind
> `register.html` are drawn from conversation with the team that operates
> it, not from reviewing that Lambda source directly. Anything not
> independently confirmed is marked **TBD**.

---

## Overview

Onboarding happens through **`register.html`**, which calls two Lambda
functions:

- An **accept** Lambda (informally referred to as `accept_dm_registration` —
  confirm exact resource name) that validates the registration **in real
  time**, including synchronously rejecting an invalid Cognito password
  before the form can proceed.
- A **fulfill** Lambda that builds and delivers a personalised
  `SML-App.exe`.

This pipeline is confirmed **separate from SML-T** — SML-T is not where
registration or password validation happens. Whether "SML-Easter Egg" (a
name that came up earlier in this project's history) refers to
`register.html` itself, a different screen, or something else entirely is
still unresolved — see [Open Items](#open-items--tbd).

There is a manual, one-time handoff step between registration and the
customer's first SML-App launch — see
[Manual Handoff ("Swivel Chair") Step](#manual-handoff-swivel-chair-step)
below.

---

## User Journey

```
Submit register.html  →  accept Lambda validates in real time, rejects a bad
Cognito password synchronously, creates the account + custid  →  fulfill
Lambda builds personalised SML-App.exe  →  zip delivered to customer's
machine  →  customer runs SML-App.exe  →  dashboard loads (local features
work immediately)  →  customer clicks "SML" to submit a job  →  app detects
no stored password  →  routes to setup.html  →  customer re-enters the SAME
password they (or an admin) proposed in register.html  →  password stored in
this machine's OS keychain  →  every subsequent launch authenticates
transparently
```

1. An admin (or the customer, depending on workflow — **TBD**) submits
   `register.html` with name/email, and **proposes** a password for the new
   Cognito account. This is the only place the password is ever set — it is
   not generated or shown by the system.
2. `register.html` posts to the **accept** Lambda, which validates the
   submission **synchronously** — critically, this includes checking the
   proposed password against Cognito and **rejecting it in real time** if
   it's invalid, before the form can proceed. This means, unlike
   `/api/setup` in SML-App (see [Password Handling](#password-handling)), a
   bad password is caught immediately at registration, not silently
   accepted.
3. On successful validation, the accept Lambda:
   - Claims a custid from the existing `dm-custid-pool` DynamoDB table
     (unchanged from the prior version of this pipeline — see
     [DynamoDB: dm-custid-pool](#dynamodb-dm-custid-pool)).
   - Creates the Cognito user in the User Pool with `custom:custid` set to
     the claimed custid.
4. The **fulfill** Lambda then builds the personalised delivery. **TBD:**
   whether "build" here means the fulfill Lambda itself invokes something
   equivalent to `build.bat`/PyInstaller server-side (closer to the old
   Lambda-built-zip model), or whether it hands off to a locally-run
   `build.bat` on an operator's machine (closer to what this document
   previously assumed). This distinction matters and should be confirmed —
   see [Open Items](#open-items--tbd).
5. However the build happens, `build.bat`'s role (confirmed from source) is:
   resolve that region's Cognito `user_pool_id` / `client_id` /
   `identity_pool_id` from `cognito-regions.json`, write a personalised
   `sml-app.config` (see [sml-app.config](#sml-appconfig)), and run
   PyInstaller (`sml-app.spec`) to produce `SML-App.exe`, packaged into
   `dist/SML-App-{custid}.zip` alongside a generated `README.txt`.
6. **The zip is delivered to the customer's machine by some manual means —
   this project has no confirmed automated delivery channel (no email, no
   presigned S3 link) as of this version.** See
   [Manual Handoff ("Swivel Chair") Step](#manual-handoff-swivel-chair-step).
7. Customer extracts the zip and runs `SML-App.exe`. `launcher.py` opens the
   browser to `http://localhost:5000`. The dashboard (`index.html`) loads
   immediately — Views, measurements, and local file management all work
   with no Cognito interaction, since `sml-app.config` already has every
   identity field `build.bat` wrote.
8. The **first** time the customer does something that needs AWS — clicking
   the SML button to submit a job — `index.html` calls `GET /api/auth/status`.
   Since no password has been stored on this machine yet, this fails, and the
   customer is redirected to `/setup.html`.
9. `setup.html` shows custid/email read-only (from `/api/config`) and asks
   only for the Cognito password. This is the step where the customer must
   re-enter the **exact same password** they or an admin proposed in
   `register.html` in step 1 — see the emphasized callout below.
10. `POST /api/setup` stores the password in this machine's OS keychain via
    `keyring` (never written to disk). From this point on, `_cognito_id_token()`
    authenticates transparently via SRP against Cognito, and no further
    password entry is needed on this machine — including across restarts,
    since the keychain entry persists at the OS level.

---

## Manual Handoff ("Swivel Chair") Step

**This is the most important operational gap to communicate to anyone running
onboarding.** Regardless of how the accept/fulfill Lambda pipeline actually
works internally, one thing is certain from `server.py`'s side: the password
proposed at registration is never written into `sml-app.config` or the built
zip, and `/api/setup` requires the customer to type it again on the target
machine.

- The password is **never** written into `sml-app.config` or the built zip.
- Whether the fulfill Lambda emails anything to the customer (a link, the
  zip itself, instructions) is **TBD** — the previous version of this
  document assumed SES delivery had been fully retired, which turned out to
  be an incorrect assumption about the whole pipeline, so this should not be
  treated as settled either way without confirming directly.
- What's confirmed either way: nothing in this pipeline transmits or stores
  the *password* itself past the moment it's proposed. Even if the zip is
  emailed automatically, the password is not part of it.

**Operationally, this means:** whoever proposes the password at registration
must write it down or copy it at that moment, because there is no way to
retrieve it afterward regardless of how the zip itself is delivered. They
will need that exact password again when they (or the customer) reach
`setup.html` on the target machine. `build.bat`'s generated `README.txt` now
carries this warning explicitly:

> IMPORTANT: Write down or copy the Cognito password you propose in
> SML-Easter Egg when your account is created - you need that exact
> password again for step 3 below, and there is no way to look it up
> afterward.

(`README.txt` currently says "SML-Easter Egg" rather than "register.html" —
left as-is pending confirmation of how that name is actually used/understood
by the people running registration; see [Open Items](#open-items--tbd).)

If the password is lost between these two steps, the only recovery path is
an admin-triggered Cognito password reset, which puts the account into
`FORCE_CHANGE_PASSWORD` — handled by `server.py`'s
`PasswordChangeRequiredError` flow and the dashboard's password-change
banner, but still requires the customer to set and remember a new password
manually.

---

## Password Handling

| Where | What happens |
|---|---|
| `register.html` → accept Lambda | Customer/admin **proposes** a password. **Confirmed:** the accept Lambda validates it against Cognito synchronously and rejects it in real time if invalid — this does not silently accept a bad password. Cognito user is created only on success. |
| Between registration and SML-App | Password exists **only** in whatever the operator wrote down at proposal time. Not stored in `sml-app.config`; whether it's transmitted any other way (email, etc.) is **TBD**. |
| `setup.html` / `POST /api/setup` | Customer re-enters the password. `server.py` stores it via `keyring.set_password(KEYRING_SERVICE, COGNITO_USERNAME, password)` — **no validation against Cognito happens here.** Since the password was already validated once at registration, the risk here is narrower than originally assumed: it's not "was this password ever valid," it's "did the customer correctly retype the password they were given." A typo at this step is still accepted and stored without error. |
| First real AWS action after setup | `_cognito_id_token()` attempts SRP authentication with the stored password. If the customer mistyped it during `setup.html`, this is the first point that failure surfaces — as a generic auth error, not a clear "wrong password" message. |

**Recommendation, not yet implemented:** add an `authenticate()` call inside
`/api/setup` before `keyring.set_password(...)`, so a retyping mistake is
rejected immediately with a clear message instead of surfacing later as a
confusing failure. This was raised during development but not yet built. It
is a smaller gap than previously documented — the password itself is
already validated once, at registration — but a second machine-side check
still catches ordinary transcription errors that the accept Lambda has no
way to see.

---

## Components

### register.html + Lambda pipeline

**TBD — not independently verified**, though more is confirmed than in the
previous revision of this document:

- Frontend: `register.html`. Whether this is the same page documented in
  Version 1.0 (with an "AWS CLI" tab and a "coming soon" Cognito tab) or has
  since been reworked is not confirmed — the CLI tab, at minimum, is
  presumably obsolete now that `auth_mode = cli` no longer exists anywhere
  in `server.py` or `build.bat`.
- Backend: two Lambdas, informally called **accept** and **fulfill**.
  - accept: validates the submission synchronously, including real-time
    Cognito password rejection (confirmed). Claims a custid from
    `dm-custid-pool` and creates the Cognito user with `custom:custid` set
    on success.
  - fulfill: builds and delivers the personalised `SML-App.exe`. Whether
    this Lambda itself performs the build (server-side PyInstaller) or
    triggers a separate, locally-run `build.bat` is **TBD** — see
    [User Journey](#user-journey), step 4.
- Whatever "SML-Easter Egg" refers to relative to this pipeline (the same
  thing as `register.html`, a different tool, an internal nickname) is
  **unresolved** — see [Open Items](#open-items--tbd).

- Collects name / email (may be pre-filled from an internal source — unclear).
- Customer proposes a password for the Cognito account.
- Detects the target platform (observed: "windows x64") and confirms it's
  the install target.
- On submit, creates the Cognito account, claims a custid, and triggers a
  build for the confirmed platform.

Whether this is a standalone tool, a feature inside SML-T, its hosting
(local script vs. deployed service), and its exact Cognito API calls are
all unconfirmed and should be documented by whoever owns that code.

---

### DynamoDB: dm-custid-pool

**Unchanged from the prior version of this pipeline — confirmed still in use.**

Pool of pre-generated unique customer IDs available for assignment.

| Attribute | Type | Notes |
|-----------|------|-------|
| `custid` | String | Partition key |
| `assigned` | String | `"false"` or `"true"` — string not boolean |
| `assigned_at` | String | ISO 8601 UTC timestamp; empty until assigned |

**GSI: assigned-index**
Partition key: `assigned` (String)
Projection: All
Used to query for unassigned custids efficiently; a conditional write claims
the first available candidate to prevent double-assignment under concurrent
load.

**Pool management:** custids are loaded in batches using
`aws dynamodb batch-write-item` with JSON files generated from a source CSV
(up to 25 items per call). A helper script (`upload_custids.bat`) iterates
over batch files and calls batch-write sequentially. **Unchanged from the
prior version of this pipeline.**

Whether `dm-registrations` (the permanent registration record table
documented in Version 1.0) is still written to by the accept Lambda is
**TBD** — not confirmed either way.

---

### build.bat

Runs once the accept Lambda has created the Cognito account and claimed a
custid. Whether it's invoked locally by an operator or triggered by the
fulfill Lambda is **TBD** (see [User Journey](#user-journey)) — what follows
is confirmed from the script's own source, regardless of what triggers it.

```
build.bat <custid> <email> [region]
```

- Resolves `user_pool_id` / `client_id` / `identity_pool_id` for the given
  region from `cognito-regions.json` (the single source of truth for these
  IDs — also read at runtime, though `/api/setup` no longer takes a region
  parameter itself; region is fixed at build time).
- Writes `sml-app.config` (see below).
- Runs `python -m PyInstaller sml-app.spec --noconfirm --clean`.
- Packages `dist/SML-App.exe` + `sml-app.config` + a generated `README.txt`
  into `dist/SML-App-{custid}/`, then zips it to
  `dist/SML-App-{custid}.zip`.

Region availability is currently limited — as of this version,
`cognito-regions.json` only has `us-east-1` configured.

---

### sml-app.spec (PyInstaller)

Bundles `index.html`, `measurements.html`, `job-plot.html`, `pdfs-table.html`,
and `setup.html` as data files, and declares hidden imports for Flask,
boto3/botocore, `keyring`, and `pycognito`.

`keyring` and `pycognito` were added to `hiddenimports` after a real bug:
both resolve some of their internals dynamically at runtime (`keyring`'s
backend, in particular, resolves via `importlib`/entry points to reach
Windows Credential Manager), which PyInstaller's static import analysis
doesn't always discover on its own. Without these declared explicitly, the
import can fail silently inside the frozen `.exe` even though both packages
are correctly installed in the build environment — `server.py`'s
`try/except ImportError` around these imports then quietly leaves them
unavailable, and every keychain-backed auth call fails with a
"package not installed" error that has nothing to do with the actual
installation.

---

### sml-app.config

Written by `build.bat`, bundled into the delivered zip. **Contains no
password.**

```ini
[identity]
custid            = {custid}
email             = {email}
cognito_username  = {email}
cognito_region     = {region}
user_pool_id      = {user_pool_id}
client_id         = {client_id}
identity_pool_id  = {identity_pool_id}

[storage]
input_bucket   = customer.decision-machine.com
output_bucket  = output.customer.decision-machine.com
watch_path     = downloads
watch_interval = 30
agent_interval = 60

[server]
port = 5000
```

`cognito_username` is always the account email — there is no separate
username field anywhere in the system.

If this file is absent or missing required identity fields on launch,
`server.py`'s `save_setup()` (`/api/setup`) refuses to proceed and reports
"This build is not provisioned" rather than silently accepting manually
typed values — provisioning happens once, at build time, not per-launch.

---

### setup.html / POST /api/setup

Shown only when `GET /api/auth/status` reports the app isn't ready — which in
practice means no password has yet been stored in this machine's OS keychain
for this `cognito_username`. Custid and email are shown read-only, sourced
from `GET /api/config`; the only input is the password. See
[Password Handling](#password-handling) above for what validation does and
doesn't happen at this step.

---

### Runtime Cognito Authentication

`server.py`'s `_cognito_id_token()` authenticates via SRP using
`pycognito.Cognito`, refreshing via `refresh_token` when possible and only
falling back to a full password login when necessary. The resulting ID token
is exchanged for scoped, temporary AWS credentials via the Cognito Identity
Pool (`_cognito_federated_credentials()`); custid-scoping to the correct S3
prefixes is enforced on the IAM role attached to the identity pool's
authenticated role via principal tags (ABAC), not by anything client-side.

This session state (`_cognito_obj`, `_boto_session`, `_creds_expiry`) is
module-level and shared across both foreground Flask request handlers and
two background threads — the S3 output-polling agent and the local
downloads-folder file watcher. A `threading.Lock` (`_cognito_lock`) guards
the entire check-refresh-cache sequence to prevent these threads racing on
the same session state; without it, one thread's in-progress authentication
could be reset by another thread's failure handler mid-call, surfacing as
authentication errors with no clear cause.

---

### custid Verification

Before any job submission (`POST /api/submit-sml`) touches S3, the app
independently confirms `sml-app.config`'s `custid` matches the authoritative
`custom:custid` attribute on the authenticated Cognito profile
(`_verify_custid_matches_cognito()`, via `cognito-idp.get_user`). This exists
because `sml-app.config` is a local file — `build.bat`'s config-writing and
Cognito's own attribute are two independently-editable sources of truth,
and nothing prevents them drifting apart (a hand-edited config file, a
mismatched build, etc.). On mismatch, the upload is blocked entirely and the
dashboard shows a persistent banner; the customer cannot submit jobs again
until the discrepancy is resolved (a new build, or contacting support).

---

## Security

- **Password storage** — never written to disk anywhere in this pipeline;
  lives only in the OS keychain on the machine `/api/setup` was run on.
- **AWS access** — no long-lived AWS credentials anywhere in SML-App. Every
  session obtains short-lived, custid-scoped credentials via Cognito
  Identity Pool federation, refreshed automatically.
- **custid integrity** — independently verified against Cognito at
  submit-time, not just trusted from the local config file.
- **S3 access** — output bucket access is scoped per-identity via IAM
  principal tags (ABAC), not bucket-wide credentials.
- **DynamoDB custid pool** — conditional writes prevent duplicate assignment
  under concurrent load. Unchanged from the prior version of this pipeline.

---

## Region

All confirmed AWS-side components (Cognito, DynamoDB custid pool, S3) are in
`us-east-1`. `cognito-regions.json` currently only has `us-east-1` configured
for build-time resolution.

---

## Open Items / TBD

- [x] ~~Confirm whether real-time password validation happens anywhere~~ —
      confirmed: yes, in the accept Lambda, synchronously, at registration.
- [x] ~~Confirm whether this pipeline is retired~~ — confirmed no: it's
      still Lambda-based, just Cognito-based now instead of CLI-based, and
      the Lambda function(s) appear to have been renamed/replaced.
- [ ] Confirm the relationship between "SML-Easter Egg" and `register.html`
      — same thing under an informal name, or two different surfaces? This
      document currently treats them as possibly-the-same and uses
      `register.html` as the primary name, but this is unconfirmed.
- [ ] Confirm exact Lambda resource names (this document uses
      "accept"/"fulfill" and `accept_dm_registration` informally/loosely)
- [ ] Confirm whether the fulfill Lambda performs the PyInstaller build
      itself, or triggers a locally-run `build.bat` — changes who/what
      machine actually needs Python/PyInstaller installed
- [ ] Confirm whether `dm-registrations` (registration record table) is
      still written to
- [ ] Confirm whether the fulfill Lambda emails anything to the customer
      (previously assumed retired; that assumption was wrong once already,
      so don't assume either way without checking)
- [ ] Decide whether to add a second, machine-side password check to
      `/api/setup` to catch retyping mistakes (see
      [Password Handling](#password-handling)) — lower priority now that
      real-time validation at registration is confirmed
- [ ] Decide whether to update `build.bat`'s generated `README.txt` wording
      from "SML-Easter Egg" to "register.html" once the naming question
      above is resolved
