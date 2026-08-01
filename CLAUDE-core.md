# CLAUDE-core.md — Core Agent

## Scope

The Core agent owns all shared infrastructure and the main dashboard.

**Files owned:**
- `server.py` — Flask REST API, file serving, JSON persistence
- `index.html` — Dashboard layout, card rendering, slug grouping, search/filter

**Files never modified by this agent:**
- `measurements.html` — owned by Views agent
- `job-plot.html` — owned by SML agent
- `pdfs-table.html` — owned by DM agent

**Subagent requests:** When Views, SML, or DM agents need a new API endpoint, a change to the filename convention, or a change to how their column renders in the dashboard, those are Core agent tasks.

---

## Running the App

```bash
cd SML-App
python3 server.py
# → http://localhost:5000
```

**Dependencies:** Flask only (`pip install flask`)

---

## Directory Layout

```
SML-App/
├── server.py
├── index.html
├── measurements.html
├── job-plot.html
├── pdfs-table.html
├── data/
│   ├── views.json          # View + series metadata (auto-created)
│   └── jobs.json           # Legacy job metadata (auto-created)
├── input/                  # Uploaded time-series CSVs
└── jobs/                   # SML/DM output CSVs (dropped by pipeline)
```

---

## REST API Reference

### Views

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/views` | List all views; backfills slugs on read |
| POST | `/api/views` | Create a view |
| PUT | `/api/views/<vid>` | Update title, category, notes, slug |
| DELETE | `/api/views/<vid>` | Delete view and its series files |

**View object shape:**
```json
{
  "id": "view-abc1234567",
  "title": "Portfolio Returns",
  "slug": "portfolioreturns",
  "category": "Equity",
  "notes": "",
  "createdAt": "2026-03-01T12:00:00+00:00",
  "series": []
}
```

### Series

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/views/<vid>/series` | Upload a CSV; auto-derives header from file |
| DELETE | `/api/views/<vid>/series/<sid>` | Delete series and its file |
| GET | `/api/views/<vid>/series/<sid>/csv` | Serve the series CSV |

**Series object shape:**
```json
{
  "id": "series-xyz9876543",
  "name": "returns.csv",
  "filename": "a1b2c3d4_returns.csv",
  "header": "open,high,low,close",
  "uploadedAt": "2026-03-01T12:00:00+00:00",
  "headers": ["date", "open", "high", "low", "close"],
  "dateRange": ["2024-01-03", "2026-03-01"],
  "length": 520
}
```

### Job Files

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/jobfiles` | Scan `jobs/` directory, parse filenames, match slugs to view IDs |
| GET | `/api/jobfiles/<filename>/csv` | Serve a job CSV |

**Jobfile object shape:**
```json
{
  "filename": "tGwuZQqEcx_portfolioreturns_binary_A-AEE_2026-03-02.csv",
  "is_sml": true,
  "is_pdfs": false,
  "parsed": true,
  "slug": "portfolioreturns",
  "process": "binary",
  "descriptor": "A-AEE_2026-03-02",
  "custid": "tGwuZQqEcx",
  "view_id": "view-abc1234567",
  "modifiedAt": "2026-03-02T14:30:00+00:00"
}
```

### Legacy Jobs (do not extend)

`/api/jobs` GET/POST and `/api/jobs/<jid>` DELETE/csv — retained for compatibility only. New development uses `/api/jobfiles`.

---

## File Naming Convention

SML and DM output files dropped into `jobs/` must follow:

```
{custid}_{slug}_{process}_{descriptor}.csv
```

| Segment | Description | Example |
|---------|-------------|---------|
| `custid` | Customer ID | `tGwuZQqEcx` |
| `slug` | Lowercase alphanumeric, matches View slug | `portfolioreturns` |
| `process` | Processing type; `pdfs` → DM column, anything else → SML column | `binary` |
| `descriptor` | Arbitrary run identifier | `A-AEE_2026-03-02_6M` |

Files that don't match the 3-segment minimum are marked `parsed: false` and appear as ungrouped.

---

## Slug System

Slugs connect Views to their output files. A View's slug is stored in `views.json`. When the pipeline drops a file, its filename slug must match the View's slug exactly.

**Slug derivation (auto):**
```python
slug = re.sub(r'[^a-z0-9]', '', title.lower())
# "Portfolio Returns" → "portfolioreturns"
```

**Backfill:** Views created before the slug field existed are backfilled on every `GET /api/views` and `GET /api/jobfiles` call, and persisted to `views.json`.

---

## CSV Formats

### View Input Files (`input/`)

Row-oriented time-series. Validated on upload by `parse_csv_meta()`.

```
date,open,high,low,close
2024-01-03,31.20,33.00,30.50,32.10
2024-01-04,30.84,32.10,30.20,31.22
```

- Row 0: headers (`date` + measurement names)
- Col 0: `YYYY-MM-DD` dates (validated)
- Col 1+: numeric values

### SML Output Files (`jobs/`, non-pdfs)

Symbol-pivoted multi-row format:

```
Symbol,TS,value,p+,p-,E,P,R,N,T,FE,therm_p+,therm_p-
AA,2024-01-03,31.20,0.6811,0.3189,...
AA,2024-01-04,30.84,0.7100,0.2900,...
BB,2024-01-03,45.10,0.5500,0.4500,...
```

- Col 0: Symbol (repeating)
- Col 1: TS (`YYYY-MM-DD`)
- Col 2+: measurements

### DM Output Files (`jobs/`, process = `pdfs`)

Arbitrary tabular CSV — rendered as a filterable table by pdfs-table.html.

---

## Dashboard (index.html)

### Layout

Three-column parallel grid. Each row is one View.

```
[ View card ] [ SML card(s) ] [ DM card(s) ]
```

Sticky column headers remain visible while scrolling. Unmatched files (no slug match) appear in an ungrouped row at the bottom.

### Key Functions

| Function | Description |
|----------|-------------|
| `loadAll()` | Fetches `/api/views` and `/api/jobfiles` in parallel |
| `renderAll()` | Renders filters, stats, and the parallel grid |
| `renderParallelGrid()` | Builds one `.parallel-row` per filtered View |
| `groupBySlug(files, filteredViews)` | Returns `Map<slug, files[]>` for matched files only |
| `renderCardInner(v)` | Returns HTML for a View card |
| `jobCellHtml(slug, filesMap, navBase, cellClass, emptyMsg)` | Returns HTML for an SML or DM cell |
| `jobFileRowHtml(f, navBase)` | Returns HTML for a single file row within a cell |
| `getFiltered()` | Returns Views matching current search + category filter |
| `renderFilters()` | Renders category filter buttons |
| `renderStats()` | Updates header stat counters |

### Navigation

- View card → `measurements.html?view=<vid>&series=<sid>`
- SML file row → `job-plot.html?file=<filename>`
- DM file row → `pdfs-table.html?file=<filename>`

### CSS Architecture

Key class names:

| Class | Description |
|-------|-------------|
| `.parallel-grid` | Flex column container for all rows |
| `.parallel-row` | CSS grid `repeat(3, 1fr)` — one row per View |
| `.prow-cell` | Cell wrapper (provides surface/border) |
| `.cell-views` | Views column cell |
| `.cell-sml` | SML column cell |
| `.cell-pdfs` | DM column cell |
| `.prow-empty` | Placeholder for views with no matching files |
| `.col-headers` | Sticky header bar |
| `.card` | View card inner content |
| `.job-card` | SML/DM file card |
| `.job-file-row` | Individual file row within a job card |

### Theme

Light/dark toggle. CSS variables in `:root` and `:root.light`. Persisted to `localStorage` as `sml_theme`. Applied on load by `loadTheme()`.

**Palette:**

| Variable | Dark | Light | Usage |
|----------|------|-------|-------|
| `--bg` | `#06090f` | `#f0f4fb` | Page background |
| `--surface` | `#0d1420` | `#ffffff` | Cards |
| `--blue` | `#2e7eff` | `#1a6ef5` | Primary accent |
| `--green` | `#2de8a0` | `#0ea572` | SML / positive |
| `--red` | `#ff4d6a` | `#e8273f` | Negative / alerts |
| `--amber` | `#ffb347` | `#d97706` | DM / warnings |

### Customer ID Badge

Displayed in the header. Read from `localStorage` key `sml_customer_id`. Set via the settings modal.

---

## server.py Key Details

### JSON persistence

```python
def read_json(path, default): ...  # returns default if file missing or corrupt
def write_json(path, obj): ...     # atomic write via path.write_text(json.dumps(...))
```

### CSV validation (`parse_csv_meta`)

Called on every series upload. Validates:
- At least 2 rows (header + data)
- At least 2 columns (date + one measurement)
- All date values match `YYYY-MM-DD`

Returns `{ headers, dateRange, length }`.

### Security

- `/api/jobfiles/<filename>/csv` rejects filenames containing `/`, `\`, or `..`
- Static file serving restricted to `{'index', 'measurements', 'job-plot', 'pdfs-table'}`

---

## Adding a New API Endpoint

1. Add route to `server.py`
2. If the endpoint affects dashboard rendering, update `loadAll()` or `renderParallelGrid()` in `index.html`
3. Document in this file under REST API Reference
4. Notify relevant subagent via their CLAUDE doc update

---

## S3 Download Agent & File Watcher

Both are background daemon threads started in `server.py`. Full behaviour is documented in `CLAUDE-architecture.md`. Key points for Core agent work:

**Delete-on-download:** After a successful download (CSV or ERROR file), the agent immediately deletes the file from `output.customer.decision-machine.com`. This is the security pledge — processing is complete when the file is downloaded — and prevents re-download on subsequent polls. Implemented in `_s3_delete_with_retry()`, which retries up to 3 times with exponential backoff (2s, 4s, 8s) before logging a warning and moving on.

**`watch_path/` is a transit buffer**, not a persistent store. Files land there briefly; the file watcher moves them to `jobs/` or `errors/`; they are deleted from S3. The watch_path scrubbing in archive/delete endpoints is still correct as cleanup, but is no longer the guard against re-download.

**IAM requirement:** `s3:DeleteObject` on `output.customer.decision-machine.com/${aws:PrincipalTag/custid}/*` must be present in the SMLAppCLIUser role policy.

---

## Regression Testing

Test suite: `SML-App-Regression-Tests.md`

Run all tests against a clean Flask instance with the Portfolio Returns seed data loaded before packaging any tester build. Any Fail must be raised as a bug before release.

When adding or changing a feature: check whether any existing test covers it and update accordingly. New features must have a corresponding test added to the suite before the PR is merged.

Sections in the test suite and the agents responsible for keeping them current:

| Section | Agent |
|---------|-------|
| 1 — Dashboard & views | Core |
| 2 — SML job submission | Core + SML |
| 3 — S3 agent & file watcher | Core |
| 4 — SML & DM cells | Core + SML + DM |
| 5 — Error files | Core |
| 6 — End-to-end flow | Core |
| 7 — UI & theme | Core |
| 8 — TSU billing flow | Core |
| 9 — Setup wizard | Core |

---

## Known Limitations

- No authentication — intended for trusted local network
- `data/jobs.json` is legacy; do not build new features on it
- No automatic cleanup of orphaned files in `input/` or `jobs/`
- Deployment packaging (PyInstaller, service registration) not yet implemented
