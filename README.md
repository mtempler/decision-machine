# Decision Machine

**Decision Machine turns raw time-series CSVs into auditable decisions using the science of counting**

Decision Machine is a pipeline system to upload, visualize, and submit time-series data for scientific analysis/visualization and decisioning, based on the science of counting. The science of counting is what emerges when we count *units of measure* and enforce what we count as constraints on maximum entropy. SML-App is the local desktop client for the Decision Machine Platform, on Windows.

\---

## What it does

```
Upload a time-series CSV  →  Review in the plot viewer  →  Submit for SML processing  →  Explore results
```

* **Views** — organize your time-series data into named views with categories and notes
* **Measurements** — interactive time-series plotter with date range selection
* **SML Processing** — submit Binary or Units analysis jobs; results appear automatically when ready
* **Decision Machine** — tabular viewer for DM output reports
* **File management** — archive, restore, and delete output files directly from the dashboard

\---

## Screenshots

|Dashboard|Job Plot|Measurements|
|-|-|-|
|!\[Dashboard](docs/screenshots/dashboard.png)|!\[Job Plot](doc/screenshots/job-plot.png)|!\[Measurements](doc/screenshots/measurements.png)|

\---

## Getting started

### Option 1 — Get a personalised build (recommended)

SML-App is distributed as a personalised Windows executable. Each build is configured with your unique customer ID and email for TSU billing.

[**→ Register to get your copy**](https://decision-machine.com/register)

You'll receive an email with a download link within minutes.

### Option 2 — Build from source

Requirements: Python 3.11+, AWS CLI configured with appropriate credentials.

```bash
git clone https://github.com/mtempler/decision-machine
cd sml-app
pip install flask boto3
python server.py
```

Open `http://localhost:5000` in your browser.

To build a distributable `.exe`:

```bash
pip install pyinstaller
build.bat {your-custid} {your-email}
```

\---

## How it works

SML-App runs a local Flask server on `localhost:5000`. Your time-series CSVs are stored on your machine in `input/`. When you submit a job:

1. The data file (header stripped) and a metadata `.ini` file are uploaded to AWS S3, leveraging AWS security infrastructure.
2. A Lambda function processes the data and writes results back to S3
3. A background agent on your machine downloads the results automatically
4. Results appear in the dashboard within minutes

\---

## Requirements

* Windows 10 or later (macOS support planned)
* AWS CLI configured (`\~/.aws/credentials`) — for CLI auth mode
* Active internet connection for SML processing and TSU billing
* No internet required for local Views, plotting, and data management

\---

## TSU Billing

SML processing is metered in **Time-Series Units (TSUs)**:

* 1 TSU = 1 time-series with 250 timestamps
* Cost: **$0.10 USD per TSU**
* Purchase TSUs from within the app — invoice sent to your registered email
* Balance displayed in the header; jobs blocked if unfunded

\---

## Data privacy

|Data|Where it lives|Retention|
|-|-|-|
|Raw time-series CSVs|Your machine only|Until you delete them|
|Processing input|AWS S3 `OnDemand/`|Deleted immediately after processing|
|Processing output|AWS S3 + your machine|Until you delete them|
|Your email|SML-App config + invoice request|Invoice request deleted after processing|

No analytics, no telemetry, no third-party data sharing.

\---

## Configuration

`sml-app.config` (created at first launch, lives next to the `.exe`):

```ini
\[identity]
custid    = your-customer-id
auth\_mode = cli          ; cli | cognito
email     = you@example.com

\[storage]
input\_bucket   = customer.decision-machine.com
output\_bucket  = output.customer.decision-machine.com
watch\_path     = downloads
watch\_interval = 30
agent\_interval = 60
```

\---

## IAM policy (CLI mode)

Your AWS credentials need the following permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": \[
    {
      "Effect": "Allow",
      "Action": \["s3:PutObject"],
      "Resource": "arn:aws:s3:::customer.decision-machine.com/OnDemand/\*"
    },
    {
      "Effect": "Allow",
      "Action": \["s3:GetObject", "s3:ListBucket"],
      "Resource": \[
        "arn:aws:s3:::output.customer.decision-machine.com",
        "arn:aws:s3:::output.customer.decision-machine.com/{your-custid}/\*"
      ]
    }
  ]
}
```

\---

## Roadmap

* \[ ] Cognito authentication (no AWS CLI required)
* \[ ] macOS support
* \[ ] Web registration portal
* \[ ] Job status tracking (pending / processing / complete)

\---

## Contributing

Contributions are welcome and appreciated — this is an open-source client for an active platform, and there's meaningful work to be done.

**What's open for contribution:**

* The local Flask server (`server.py`) and all UI code
* The background download agent
* Build tooling (`build.bat`, PyInstaller config)
* Documentation and examples

*Note: The SML processing Lambda functions and Decision Machine backend infrastructure are proprietary and not part of this repository.*

**Where help is most wanted right now:**

* 🍎 **macOS port** — the biggest item on the roadmap; Python/Flask experience helpful
* 🧪 **Testing** — unit and integration tests are thin; pytest contributions welcome
* 🎨 **UI improvements** — the frontend is functional but unpolished
* 📖 **Docs** — usage examples, tutorials, and annotated screenshots

**Getting started:**

1. Follow the [build from source](#option-2--build-from-source) instructions
2. Browse [open issues](https://github.com/mtempler/decision-machine/issues) — look for ones tagged `good first issue`
3. Open a draft PR early if you're working on something significant — happy to collaborate before it's finished

For questions about the SML pipeline or billing, contact [support@decision-machine.com](mailto:support@decision-machine.com).

\---

## License

MIT License — see [LICENSE](LICENSE) for details.

The SML processing Lambda functions and Decision Machine infrastructure are proprietary and not included in this repository.

