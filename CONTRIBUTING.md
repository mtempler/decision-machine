# Contributing to Decision Machine

Thank you for your interest in contributing.

## What's in this repository

This repository contains the SML-App client — the local Flask application, HTML dashboard, and build tooling. The SML processing pipeline (Lambda functions, Decision Machine infrastructure) is proprietary and not included.

## Getting started

```bash
git clone https://github.com/mtempler/decision-machine
cd sml-app
pip install flask boto3
python server.py
```

You can use SML-App fully without AWS credentials for local features — Views, CSV upload, and the plot viewer all work offline.

## Reporting issues

Please use GitHub Issues. Include:
- Windows version
- Steps to reproduce
- What you expected vs what happened
- Any error messages from the console window

## Pull requests

- Keep changes focused — one concern per PR
- Test against a running Flask instance before submitting
- Don't commit `sml-app.config`, `data/`, `input/`, `jobs/`, or any customer data
- The `build.bat` / `build-all.bat` scripts are for internal use — changes there need discussion first

## Questions

For questions about SML processing, billing, or getting a custid: [support@decision-machine.com](mailto:support@decision-machine.com)
