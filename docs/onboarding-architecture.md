# Decision Machine — Onboarding Architecture

Version 1.0 · March 2026

---

## Overview

The onboarding pipeline registers new Decision Machine users, assigns a unique customer ID, delivers a pre-configured SML-App to their inbox, and records the registration for billing and support purposes. It is fully serverless and operates independently of the SML-App platform.

---

## User Journey

```
Register on web  →  Receive email  →  Download zip  →  Extract & run
```

1. User visits `register.html` and submits name, email, and AWS Account ID
2. The registration Lambda processes the request and assigns a custid
3. The Lambda builds a personalised zip containing `SML-App.exe` and a pre-filled `sml-app.config`
4. The Lambda uploads the zip to the customer's S3 directory and generates a 7-day presigned download URL
5. The Lambda sends a confirmation email with the download link
6. User downloads `SML-App.zip`, extracts to a folder, and runs `SML-App.exe`
7. The app starts directly to the dashboard — no further configuration required

---

## Architecture Diagram

```
                        ┌─────────────────────────────────────┐
                        │         register.html (S3)          │
                        │  www.decision-machine.com bucket     │
                        └──────────────┬──────────────────────┘
                                       │ POST /prod/register
                                       ▼
                        ┌─────────────────────────────────────┐
                        │   API Gateway HTTP API              │
                        │   dm-registration                   │
                        │   prod stage                        │
                        └──────────────┬──────────────────────┘
                                       │ Lambda proxy
                                       ▼
                        ┌─────────────────────────────────────┐
                        │   Lambda: dm-registration           │
                        │                                     │
                        │  1. Validate input                  │
                        │  2. Claim custid from pool          │
                        │  3. Write registration record       │
                        │  4. Build & upload SML-App.zip      │
                        │  5. Generate presigned URL          │
                        │  6. Send confirmation email         │
                        └──┬──────────┬──────────┬───────────┘
                           │          │          │
                           ▼          ▼          ▼
             ┌─────────────────┐  ┌───────┐  ┌──────────────────────┐
             │    DynamoDB     │  │  S3   │  │   SES                │
             │                 │  │       │  │                      │
             │ dm-registrations│  │output.│  │ noreply@             │
             │ dm-custid-pool  │  │  ...  │  │ decision-machine.com │
             └─────────────────┘  └───────┘  └──────────────────────┘
```

---

## Components

### register.html

Hosted on S3 at `https://s3.us-east-1.amazonaws.com/www.decision-machine.com/register.html`.

Presents two tabs:

- **AWS CLI** — active. Collects name, email, and 12-digit AWS Account ID. POSTs to API Gateway on submit.
- **Cognito** — coming soon. Collects name and email for waitlist notification. Submit is disabled.

On success, displays a confirmation panel with the registrant's email address. No page reload.

---

### API Gateway

| Property | Value |
|----------|-------|
| Type | HTTP API |
| Name | `dm-registration` |
| Route | `POST /prod/register` |
| Stage | `prod` |
| Integration | Lambda proxy — `dm-registration` |
| CORS origin | `https://s3.us-east-1.amazonaws.com` |
| Invoke URL | `https://gw5lrum2p9.execute-api.us-east-1.amazonaws.com/prod/register` |

---

### Lambda: dm-registration

**Runtime:** Python 3.11  
**Source:** `lambda_function.py`

**Execution flow:**

1. **Validate** — checks name, email present; checks aws_account_id present for CLI mode
2. **Assign custid** — queries `dm-custid-pool` GSI (`assigned = "false"`) for up to 10 candidates; claims the first available using a conditional DynamoDB write to prevent race conditions; retries on contention
3. **Write registration** — puts item to `dm-registrations` with custid, name, email, auth_mode, aws_account_id, registered_at
4. **Build zip** — streams `SML-App.exe` from S3 root into memory; writes a zip containing `SML-App/SML-App.exe` and `SML-App/sml-app.config` (pre-filled with custid and email)
5. **Upload zip** — puts `{custid}/SML-App.zip` to `output.customer.decision-machine.com`
6. **Presigned URL** — generates a 7-day GET presigned URL for the zip
7. **Send email** — sends confirmation via SES with download instructions and the presigned URL

**IAM permissions required:**

| Service | Action | Resource |
|---------|--------|----------|
| DynamoDB | `Query`, `UpdateItem` | `dm-custid-pool` |
| DynamoDB | `PutItem` | `dm-registrations` |
| S3 | `GetObject` | `output.customer.decision-machine.com/SML-App.exe` |
| S3 | `PutObject` | `output.customer.decision-machine.com/{custid}/*` |
| SES | `SendEmail` | `arn:aws:ses:us-east-1:{account}:identity/decision-machine.com` |

---

### DynamoDB: dm-registrations

Permanent record of all registrations.

| Attribute | Type | Notes |
|-----------|------|-------|
| `custid` | String | Partition key |
| `name` | String | |
| `email` | String | |
| `auth_mode` | String | `cli` or `cognito` |
| `aws_account_id` | String | CLI mode only; blank for Cognito |
| `registered_at` | String | ISO 8601 UTC timestamp |

---

### DynamoDB: dm-custid-pool

Pool of pre-generated unique customer IDs available for assignment.

| Attribute | Type | Notes |
|-----------|------|-------|
| `custid` | String | Partition key |
| `assigned` | String | `"false"` or `"true"` — string not boolean |
| `assigned_at` | String | ISO 8601 UTC timestamp; empty until assigned |

**GSI: assigned-index**  
Partition key: `assigned` (String)  
Projection: All  
Used by Lambda to query for unassigned custids efficiently.

**Pool management:** custids are loaded in batches using `aws dynamodb batch-write-item` with JSON files generated from a source CSV. Each batch-write call supports up to 25 items. A helper script (`upload_custids.bat`) iterates over batch files and calls batch-write sequentially.

---

### S3: output.customer.decision-machine.com

Stores SML-App deliverables and customer output files.

| Path | Contents |
|------|----------|
| `SML-App.exe` | Generic current build — source for zip assembly |
| `{custid}/SML-App.zip` | Personalised zip delivered to customer |
| `{custid}/*.csv` | SML processing output files (managed by SML platform) |

The presigned URL delivered to the customer points to `{custid}/SML-App.zip`. Expiry is 7 days.

---

### SES

| Property | Value |
|----------|-------|
| Region | `us-east-1` |
| Sender | `noreply@decision-machine.com` |
| Verified identity | `decision-machine.com` (domain) |
| Mode | Production (sandbox disabled) |

Email contains:
- Download link (presigned URL, 7-day expiry)
- Step-by-step instructions: download → extract → run
- Customer ID for reference

---

## sml-app.config

Generated by the Lambda and bundled into `SML-App.zip`. Pre-filled with the customer's assigned custid and registered email address.

```ini
[identity]
custid    = {custid}
auth_mode = cli
email     = {email}

[storage]
input_bucket   = customer.decision-machine.com
output_bucket  = output.customer.decision-machine.com
watch_path     = downloads
watch_interval = 30
agent_interval = 60
```

When the user extracts the zip and runs `SML-App.exe`, the app reads this file at startup and proceeds directly to the dashboard. If the config is absent or incomplete, the setup wizard (`setup.html`) prompts for custid and email on first launch.

---

## Security

- **S3 access** — output bucket is private; all customer access via presigned URLs only
- **DynamoDB access** — Lambda role scoped to minimum required actions and specific table ARNs
- **Custid assignment** — conditional writes prevent duplicate assignment under concurrent load
- **SES** — domain-verified sender; production access required before go-live
- **API Gateway** — CORS restricted to `https://s3.us-east-1.amazonaws.com`; no API key required for registration (public endpoint)

---

## Region

All components deployed in `us-east-1`.

---

## Dependencies and Go-Live Checklist

- [ ] SES domain verification — `decision-machine.com` DNS CNAME records added and propagated
- [ ] SES sender verification — `noreply@decision-machine.com` confirmed
- [ ] SES production access approved (out of sandbox)
- [ ] `SML-App.exe` uploaded to root of `output.customer.decision-machine.com`
- [ ] `dm-custid-pool` seeded with sufficient custids
- [ ] `register.html` uploaded to `www.decision-machine.com` S3 bucket
- [ ] End-to-end registration test passed
