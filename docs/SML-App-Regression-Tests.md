# SML-App Regression Tests

> Run all tests against a clean Flask instance with the Portfolio Returns seed data loaded. Record Pass / Fail in the Result column. Any Fail must be raised as a bug before packaging a tester build.

## Result legend

| Symbol | Meaning |
|--------|---------|
| □ | Not yet run |
| Pass | Test passed |
| Fail | Test failed — raise bug |

---

# 1  Dashboard & views

## 1.1  Page load

| ID | Area | Description | Steps | Expected | Result |
|----|------|-------------|-------|----------|--------|
| T-101 | Config | Customer ID displays in header | Open http://localhost:5000 | Header shows custid from sml-app.config, not "Set Customer ID" | □ |
| T-102 | Config | Customer ID badge is not clickable | Hover and click custid badge | No modal opens; cursor is default | □ |
| T-103 | Load | Views load on startup | Open dashboard · Observe parallel grid | Portfolio Returns view appears with AA series row | □ |
| T-104 | Load | SML column populates | Observe SML Measurements column | Job files appear linked to Portfolio Returns row | □ |
| T-105 | Load | DM column populates | Observe Decision Machine column | PDFs job file appears linked to Portfolio Returns row | □ |
| T-106 | Load | Stats bar correct | Observe stats bar | Views, Series, Categories, SML Jobs, DM Outputs counts accurate | □ |

## 1.2  Create / edit / delete view

| ID | Area | Description | Steps | Expected | Result |
|----|------|-------------|-------|----------|--------|
| T-111 | View | Create new view | Click New View · Enter title "Test View", category "Test" · Click Create View | New view appears; slug auto-derived from title | □ |
| T-112 | View | Slug auto-derives from title | Type title in New View modal · Observe Slug field | Slug updates in real time, lowercase, spaces stripped | □ |
| T-113 | View | Title required | Open New View modal · Leave title blank · Click Create View | Toast error: "Title is required" | □ |
| T-114 | View | Category required | Open New View modal · Fill title, leave category blank · Click Create View | Toast error: "Category is required" | □ |
| T-115 | View | Edit view | Click edit icon on Portfolio Returns · Change notes · Click Save Changes | Changes saved; view updates without page reload | □ |
| T-116 | View | Delete view | Click delete icon on Test View · Confirm delete | View removed from grid; stats bar updates | □ |
| T-117 | View | Category filter | Create views in two categories · Click a category filter button | Only views matching selected category shown | □ |
| T-118 | View | Search filter | Type partial view title in search box | Grid filters to matching views in real time | □ |

## 1.3  CSV upload

| ID | Area | Description | Steps | Expected | Result |
|----|------|-------------|-------|----------|--------|
| T-121 | Upload | Upload valid CSV | Click Upload CSV on Portfolio Returns · Select AA_2026-03-02_6M.csv · Click Add Series | Series appears with correct row count and header | □ |
| T-122 | Upload | Measurements locked from headers | After upload, open SML modal · Check Measurements field | Shows column names from CSV, not editable | □ |
| T-123 | Upload | Reject non-CSV | Open upload modal · Select .xlsx or .txt file | Error: "Only CSV files are supported" | □ |
| T-124 | Upload | Reject bad date format | Upload CSV with non-YYYY-MM-DD date column | Error message identifying the bad row | □ |
| T-125 | Upload | Reject non-numeric data | Upload CSV with text in numeric column | Error message identifying column and row | □ |
| T-126 | Upload | Drag and drop | Drag a valid CSV onto the drop zone | File accepted and validated same as click-to-select | □ |
| T-127 | Upload | Delete series | Click ✕ on a series row in view card | Series removed; CSV deleted from input/; count updates | □ |

---

# 2  SML job submission

## 2.1  Submit modal

| ID | Area | Description | Steps | Expected | Result |
|----|------|-------------|-------|----------|--------|
| T-201 | Modal | Open SML modal | Hover series row · Click SML button | Modal opens with series name shown, measurements locked | □ |
| T-202 | Modal | Binary process selected | Select Binary in modal | Process label highlights amber; no Advanced or T_R section | □ |
| T-203 | Modal | Units process selected | Select Units in modal | Process label highlights amber | □ |
| T-204 | Modal | Process required | Open SML modal · Click Submit Job without selecting process | Error: "Select a process (Binary or Units)" | □ |
| T-206 | Modal | Close modal | Click ✕ or Cancel | Modal closes cleanly; no state leak on reopen | □ |

## 2.2  .ini file generation & header injection

| ID | Area | Description | Steps | Expected | Result |
|----|------|-------------|-------|----------|--------|
| T-211 | .ini | Binary .ini structure | Submit Binary job · Check S3 OnDemand/ for config_*.ini | [Default] contains FileOutput, Measurements, Crumbs only — no Binary section, no T_R, no Memory; filename is config_{descriptor}.ini | □ |
| T-211b | .ini | Binary and Units .ini identical in content | Submit one Binary and one Units job · Compare .ini files in S3 | Both contain exactly FileOutput, Measurements, Crumbs — only filename prefix differs | □ |
| T-212 | .ini | Units .ini structure | Submit Units job · Check S3 OnDemand/ for measure_*.ini | [Default] contains FileOutput, Measurements, Crumbs only; filename is measure_{descriptor}.ini | □ |
| T-213 | .ini | Crumbs field correct | Submit Binary for portfolioreturns · Inspect .ini | Crumbs = tGwuZQqEcx_portfolioreturns_binary | □ |
| T-214 | .ini | FileOutput correct | Inspect any .ini | FileOutput = output.customer.decision-machine.com/tGwuZQqEcx | □ |
| T-215 | .ini | Data file has no header | Submit job · Check uploaded .csv in S3 OnDemand/ | Data file starts with first data row, not column headers | □ |
| T-216 | .ini | Upload sequence | Submit job · Monitor S3 OnDemand/ events | Data CSV appears before .ini; .ini is last | □ |
| T-217 | .ini | No custid = error | Remove custid from sml-app.config · Submit job | Server returns 400: "custid not configured" | □ |
| T-218 | Header | Binary output CSV served with header | Open Binary job in job-plot.html · Inspect network tab /api/jobfiles/*/csv | Response first line: Symbol,TS,value,p+,p-,energy,power,resistance,noise,T,FE,therm_p+,therm_p- | □ |
| T-219 | Header | Units output CSV served with header | Open Units job in job-plot.html · Inspect network tab /api/jobfiles/*/csv | Response first line: Symbol,TS,value,p,E,T,T_B,exp_n,...,cov_n_strain | □ |
| T-220 | Header | Header not written to disk | Submit job · Inspect file in jobs/ directory directly | File on disk has no header row — first line is data | □ |

---

# 3  S3 agent & file watcher

## 3.1  Download agent

| ID | Area | Description | Steps | Expected | Result |
|----|------|-------------|-------|----------|--------|
| T-301 | Agent | Agent starts on Flask launch | Start Flask · Check console | Log: "S3 agent started — polling s3://output... every 60s" | □ |
| T-302 | Agent | Watcher starts on Flask launch | Start Flask · Check console | Log: "File watcher started — polling [watch_path] every 30s" | □ |
| T-303 | Agent | Downloads new output CSV | Submit job · Wait for Lambda · Wait ≤60s | New {custid}_*.csv appears in watch_path | □ |
| T-304 | Agent | Custid filter enforced | Place file not starting with custid_ in S3 output prefix | File not downloaded; debug log shows "custid mismatch" | □ |
| T-305 | Agent | No duplicate downloads | Wait for second agent poll after file downloaded | File not downloaded again | □ |
| T-306 | Agent | Downloads error files | Trigger Lambda error · Wait ≤60s | ERROR_*.txt appears in watch_path/errors/ | □ |

## 3.2  File watcher

| ID | Area | Description | Steps | Expected | Result |
|----|------|-------------|-------|----------|--------|
| T-311 | Watcher | Copies CSV to jobs/ | Agent downloads file · Wait ≤30s | File appears in jobs/ | □ |
| T-312 | Watcher | Copies error file to errors/ | Agent downloads ERROR_*.txt · Wait ≤30s | File appears in errors/ | □ |
| T-313 | Watcher | No duplicate copies | Wait for second watcher poll | File not copied again | □ |
| T-314 | Watcher | Pre-existing files ignored | Restart Flask with files already in watch_path | Pre-existing files not re-copied to jobs/ | □ |
| T-314b | Watcher | Orphaned files recovered on restart | Place file in watch_path not yet in jobs/ · Start Flask · Wait ≤30s | File copied to jobs/ on first watcher cycle; log shows "Startup: queued orphaned file" | □ |

---

# 4  Dashboard — SML & DM cells

## 4.1  File display & navigation

| ID | Area | Description | Steps | Expected | Result |
|----|------|-------------|-------|----------|--------|
| T-401 | Display | New job file appears in SML column | Copy valid binary/units CSV into jobs/ · Refresh dashboard | File row appears in correct SML cell, linked to view by slug | □ |
| T-402 | Display | PDFs file appears in DM column | Copy valid pdfs CSV into jobs/ · Refresh dashboard | File row appears in DM cell | □ |
| T-403 | Display | Binary plot opens to value | Click a Binary file in SML column | job-plot.html opens with value selected in dropdown | □ |
| T-403b | Display | Units plot opens to value | Click a Units file in SML column | job-plot.html opens with value selected in dropdown | □ |
| T-404 | Display | Binary dropdown order | Open Binary job · Inspect measurement dropdown | Order: value, probabilities, energy, power, resistance, noise, free_energy & temperature, thermal probabilities | □ |
| T-404b | Display | Units dropdown order | Open Units job · Inspect measurement dropdown | Order: value, p, E, T, T_B, exp_n, exp_strain, exp_demand, sus_n, sus_strain, sus_E, sus_demand, var_n, var_strain, var_E, var_del_n, cov_n_strain | □ |
| T-405 | Display | Low data warning threshold | Open job with ≤110 timestamps · Open job with >110 timestamps | Warning shown only for ≤110; hidden for >110 | □ |

## 4.2  File management

| ID | Area | Description | Steps | Expected | Result |
|----|------|-------------|-------|----------|--------|
| T-411 | Mgmt | Delete individual file | Hover file row · Click ✕ button · Refresh dashboard | File removed from dashboard; deleted from jobs/ and watch_path; does not reappear on refresh | □ |
| T-412 | Mgmt | Archive all | Click Archive all button in cell footer · Refresh dashboard | All files move to archived section; jobs/ files moved to archive/; watch_path copies removed; files do not reappear on refresh | □ |
| T-413 | Mgmt | Archive toggle hidden when no archived files | Observe cell with no archived files | "N archived" toggle not shown | □ |
| T-414 | Mgmt | Archive toggle appears after archive-all | Archive all files in a cell | "N archived" toggle appears | □ |
| T-415 | Mgmt | Expand archived files | Click "N archived" toggle | Archived file rows appear in muted/dashed style | □ |
| T-416 | Mgmt | Restore or delete archived file | Expand archive · Click ↩ restore on one file · Click ✕ delete on another | Restored file appears in active list and jobs/; deleted file removed from archive/ and panel | □ |
| T-417 | Mgmt | Archive state preserved across restart | Archive all files · Restart Flask · Refresh dashboard | Archived files still shown in archive panel; no files re-appear in active cells | □ |
| T-418 | Mgmt | Deleted file does not reappear after S3 agent poll | Delete a file from dashboard · Wait 60s for agent poll · Refresh dashboard | File does not reappear — removed from watch_path so agent cannot re-copy it | □ |
| T-419 | Mgmt | Archived file does not reappear after S3 agent poll | Archive all files · Wait 60s for agent poll · Refresh dashboard | Files remain archived — not re-copied to jobs/ on next poll | □ |
| T-420 | Mgmt | DM output files follow same delete/archive behaviour | Delete a DM file from Decision Machine column · Refresh dashboard | DM file removed permanently; does not reappear on refresh | □ |

---

# 5  Error files

| ID | Area | Description | Steps | Expected | Result |
|----|------|-------------|-------|----------|--------|
| T-501 | Errors | Error panel hidden when no errors | Fresh dashboard with no error files | No red error panel visible | □ |
| T-502 | Errors | Error panel appears when error file present | Copy ERROR_*.txt into errors/ · Refresh | Red error panel appears at bottom spanning full width | □ |
| T-503 | Errors | Error count shown in panel header | Add 2 error files · Refresh | Panel header shows "2 Pipeline Errors" | □ |
| T-504 | Errors | Hover shows error message tooltip | Hover an error file row | Tooltip appears with single-line error message from file | □ |
| T-505 | Errors | EC codes display correctly | Create error files with each EC code · Hover each | Full message visible for EC-150, 155, 160, 200, 250, 350 | □ |
| T-506 | Errors | Dismiss error file | Click ✕ on error row | Row removed; file deleted from errors/; panel hides if empty | □ |
| T-507 | Errors | End-to-end error flow | Submit job that will trigger Lambda error · Wait ≤60s | ERROR_*.txt downloaded, copied to errors/, appears on dashboard with correct message | □ |

---

# 6  End-to-end flow

> **Setup:** Requires active AWS credentials, S3 access, and deployed Lambda.

| ID | Area | Description | Steps | Expected | Result |
|----|------|-------------|-------|----------|--------|
| T-601 | E2E | Binary job full cycle | Upload AA_2026-03-02_6M.csv to Portfolio Returns · Click SML → Binary → Submit Job · Wait for Lambda (<2 min) · Wait for S3 agent (≤60s) · Wait for file watcher (≤30s) · Refresh dashboard | Binary output CSV appears in SML column; job-plot.html opens and renders correctly; opens to value | □ |
| T-602 | E2E | Units job full cycle | Same as T-601 but select Units process | Units output CSV appears in SML column; correct 19-column header served | □ |
| T-603 | E2E | Error recovery | Submit job with invalid config to trigger Lambda error · Wait for agent cycle | ERROR_*.txt appears in error panel with correct EC code message | □ |
| T-604 | E2E | Multiple views multiple jobs | Create second view with different slug · Submit Binary and Units for both views · Refresh | Each view row shows its own outputs only; no cross-contamination between slugs | □ |

---

# 7  UI & theme

| ID | Area | Description | Steps | Expected | Result |
|----|------|-------------|-------|----------|--------|
| T-701 | UI | Dark/light theme toggle | Click moon/sun icon in header | Theme switches; persists on reload | □ |
| T-702 | UI | Modal close on overlay click | Open any modal · Click outside modal | Modal closes cleanly | □ |
| T-703 | UI | Toast notifications | Perform any create/delete action | Toast appears bottom-right, auto-dismisses after ~3s | □ |
| T-704 | UI | Responsive layout | Resize browser to <960px wide | Grid stacks to single column; no horizontal overflow | □ |

---

# 8  TSU billing flow

> **Setup:** Run these tests in Stripe Test Mode using sk_test_... key. Use test card 4242 4242 4242 4242 to pay invoices. No real money moves. Requires TSU-Request and TSU-Paid Lambdas deployed with test credentials.

## 8.1  TSU balance display

| ID | Area | Description | Steps | Expected | Result |
|----|------|-------------|-------|----------|--------|
| T-801 | TSU | Balance badge visible in header | Open dashboard | TSU badge appears in header alongside custid | □ |
| T-802 | TSU | Unfunded state — badge | Start with no fundedTSUs.txt in output bucket · Open dashboard | Badge shows "TSU: —" | □ |
| T-803 | TSU | Funded state — badge | Place a fundedTSUs.txt with Balance 500 in output/{custid}/ · Wait for S3 agent poll · Refresh dashboard | Badge shows "TSU: 500" | □ |
| T-804 | TSU | Zero balance — badge turns red | Set balance to 0 in fundedTSUs.txt | Badge border turns red | □ |
| T-805 | TSU | Badge click opens request modal | Click TSU badge | TSU request modal opens showing current balance and email | □ |

## 8.2  TSU request modal

| ID | Area | Description | Steps | Expected | Result |
|----|------|-------------|-------|----------|--------|
| T-811 | TSU | Email pre-populated | Open TSU modal | Email field shows address from sml-app.config | □ |
| T-812 | TSU | Pricing displayed | Open TSU modal | Modal states "Each TSU costs $0.10 USD" | □ |
| T-813 | TSU | Default quantity is 500 | Open TSU modal | Quantity field defaults to 500 | □ |
| T-814 | TSU | Quantity validation | Enter 0 or negative quantity · Click Request TSUs | Error: "Enter a quantity greater than zero" | □ |
| T-815 | TSU | Successful request — modal auto-closes | Enter quantity 50 · Click Request TSUs | Success message shown for ~1.5s then modal closes; toast appears "Invoice sent for 50 TSUs" | □ |
| T-816 | TSU | TSURequest file uploaded to S3 | Submit request · Check S3 OnDemand/ | TSURequest_{custid}_{timestamp}.txt present briefly then deleted by Lambda | □ |
| T-817 | TSU | TSURequest file deleted after processing | Monitor S3 OnDemand/ after request | File no longer present within 30s of upload | □ |

## 8.3  SML modal TSU integration

| ID | Area | Description | Steps | Expected | Result |
|----|------|-------------|-------|----------|--------|
| T-821 | TSU | Cost shown in SML modal | Open SML modal with a series having 1 symbol · Observe TSU summary | Shows "Cost 1 TSUs · Balance N → After N-1" | □ |
| T-822 | TSU | After balance turns red on overdraft | Open SML modal where cost > balance | After value shown in red | □ |
| T-823 | TSU | Never funded — submit blocked | Remove fundedTSUs.txt from S3 and locally · Open SML modal | Error shown, request flow revealed, Submit button hidden | □ |
| T-824 | TSU | Overdrawn >100 — submit blocked | Set balance to -101 in fundedTSUs.txt · Open SML modal | Error: "Account overdrawn by 101 TSUs. Please contact support." No request flow shown. | □ |
| T-825 | TSU | Zero balance — submit blocked, request shown | Set balance to 0 · Open SML modal | Submit hidden, request flow shown inline in modal | □ |
| T-826 | TSU | Positive balance — submit allowed | Set balance to 100 · Open SML modal | Submit Job button visible and active | □ |

## 8.4  Lambda — TSU-Request

> **Note:** These tests require CloudWatch access to verify Lambda execution.

| ID | Area | Description | Steps | Expected | Result |
|----|------|-------------|-------|----------|--------|
| T-831 | Lambda | S3 trigger fires on TSURequest upload | Upload TSURequest_*.txt to OnDemand/ · Check CloudWatch | Log shows "Processing TSU request: OnDemand/TSURequest_..." | □ |
| T-832 | Lambda | Non-TSU files ignored | Upload a config_*.ini to OnDemand/ · Check CloudWatch | Log shows "Skipping non-TSU file" | □ |
| T-833 | Lambda | Stripe customer created | Submit first request for a new custid · Check Stripe Dashboard → Customers | New customer created with custid in metadata | □ |
| T-834 | Lambda | Stripe customer reused | Submit second request for same custid · Check Stripe Dashboard → Customers | No new customer created; existing customer used | □ |
| T-835 | Lambda | Invoice created with correct amount | Submit request for 50 TSUs · Check Stripe Dashboard → Invoices | Invoice shows $5.00 (50 × $0.10); line item: "50 TSUs — OnDemand @ $0.10 each" | □ |
| T-836 | Lambda | Invoice sent to customer email | Submit request · Check email inbox | Invoice email received from Stripe with payment link | □ |
| T-837 | Lambda | PENDING entry written to fundedTSUs.txt | Submit request · Check S3 output/{custid}/fundedTSUs.txt | Line contains PENDING, quantity, invoice ID | □ |
| T-838 | Lambda | Idempotency — no duplicate invoices | Upload same TSURequest file twice within 1 minute · Check Stripe Dashboard | Only one invoice created; second invocation returns existing invoice | □ |
| T-839 | Lambda | TSURequest file deleted after processing | Submit request · Check S3 OnDemand/ | File deleted; not present after Lambda completes | □ |

## 8.5  Lambda — TSU-Paid (Stripe webhook)

| ID | Area | Description | Steps | Expected | Result |
|----|------|-------------|-------|----------|--------|
| T-841 | Webhook | Webhook signature verified | Send request with invalid signature to Lambda URL | Lambda returns 400; no processing occurs | □ |
| T-842 | Webhook | invoice.paid triggers credit | Pay test invoice with card 4242 4242 4242 4242 · Check CloudWatch | Log shows "Invoice paid" and "TSU credit applied" | □ |
| T-843 | Webhook | PENDING converted to PAID | Pay invoice · Check fundedTSUs.txt in S3 | PENDING line for that invoice now reads PAID | □ |
| T-844 | Webhook | CREDIT entry written with balance | Pay invoice for 50 TSUs from zero balance · Check fundedTSUs.txt | CREDIT line shows +50, Balance 50 | □ |
| T-845 | Webhook | Balance accumulates correctly | Pay two invoices: 50 then 100 TSUs · Check fundedTSUs.txt | Final Balance 150 | □ |
| T-846 | Webhook | Missing custid metadata handled | Send synthetic invoice.paid with no custid metadata · Check CloudWatch | Warning logged; no crash; no file written | □ |

## 8.6  End-to-end TSU flow

> **Setup:** Full flow from request through to balance update in SML-App. Requires Stripe test mode, deployed Lambdas, running Flask with S3 agent.

| ID | Area | Description | Steps | Expected | Result |
|----|------|-------------|-------|----------|--------|
| T-851 | E2E-TSU | Full TSU purchase cycle | Ensure fundedTSUs.txt absent or balance = 0 · Click TSU badge → enter 50 → Request TSUs · Pay invoice in Stripe test mode with card 4242 4242 4242 4242 · Wait for webhook → Lambda → S3 write (≤30s) · Wait for S3 agent poll (≤60s) · Wait for file watcher (≤30s) · Refresh dashboard | TSU badge shows 50; SML modal shows positive balance; Submit Job button active | □ |
| T-852 | E2E-TSU | TSU balance survives Flask restart | Fund TSUs and confirm balance · Restart Flask · Refresh dashboard | Balance still shown correctly after restart | □ |
| T-853 | E2E-TSU | Idempotency across multiple S3 triggers | Submit TSU request · Monitor Stripe Dashboard for 5 minutes | Exactly one invoice created regardless of how many times Lambda was triggered | □ |

---

# 9  Setup wizard

## 9.1  First launch & config writing

| ID | Area | Description | Steps | Expected | Result |
|----|------|-------------|-------|----------|--------|
| T-901 | Setup | No config → setup page shown | Rename or delete sml-app.config · Start Flask and open http://localhost:5000 | setup.html served; custid and email fields visible | □ |
| T-902 | Setup | Valid submission writes sml-app.config | On setup.html enter custid "testcust" and email "test@example.com" · Click Save & Launch · Inspect sml-app.config on disk | Config written with correct custid and email; success screen shows restart prompt | □ |
| T-903 | Setup | Validation — custid required | Leave custid blank, enter valid email · Click Save & Launch | Error shown: "Please enter your customer ID"; no config written | □ |
| T-904 | Setup | Validation — email required | Enter valid custid, leave email blank · Click Save & Launch | Error shown: "Please enter your email address"; no config written | □ |
| T-905 | Setup | Valid config present → index.html served directly | Ensure sml-app.config has valid custid and email · Start Flask and open http://localhost:5000 | index.html served; setup.html not shown | □ |

---

# Sign-off

| Tester | Date | Build | Notes |
|--------|------|-------|-------|
| | | | |
