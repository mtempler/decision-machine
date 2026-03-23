"""
SML-App Flask server
Directory layout:
  server.py
  index.html / measurements.html / job-plot.html
  data/views.json   ← auto-created
  data/jobs.json    ← auto-created
  input/            ← uploaded time-series CSVs
  jobs/             ← SML measurement CSVs (dropped here by pipeline)
"""
import sys, json, os, uuid, csv, re, configparser, io, threading, time, shutil, logging
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, abort, jsonify, request, send_file, send_from_directory

# ── Config ────────────────────────────────────────────────
_cfg = configparser.ConfigParser()
_cfg_path = Path(os.getcwd()) / 'sml-app.config' if getattr(sys, 'frozen', False) \
            else Path(__file__).parent / 'sml-app.config'
_cfg.read(_cfg_path)

def cfg(section, key, fallback=''):
    return _cfg.get(section, key, fallback=fallback)

AUTH_MODE      = cfg('identity', 'auth_mode',     fallback='cli')
CUSTID         = cfg('identity', 'custid',         fallback='')
EMAIL          = cfg('identity', 'email',           fallback='')
CLI_ROLE_ARN   = cfg('identity', 'cli_role_arn',   fallback='')
INPUT_BUCKET   = cfg('storage',  'input_bucket',   fallback='customer.decision-machine.com')
OUTPUT_BUCKET  = cfg('storage',  'output_bucket',  fallback='output.customer.decision-machine.com')
WATCH_PATH     = cfg('storage',  'watch_path',     fallback='')
WATCH_INTERVAL = int(cfg('storage', 'watch_interval', fallback='30'))
AGENT_INTERVAL = int(cfg('storage', 'agent_interval', fallback='60'))

# ── boto3 session with assumed role (CLI mode) ────────
# Assumes SMLAppCLIUser role with custid as session tag for per-custid S3/DynamoDB scoping.
# Falls back to ambient credentials if cli_role_arn not configured (dev/test).
_boto_session = None

def get_boto_session():
    global _boto_session
    if _boto_session is not None:
        return _boto_session
    if CLI_ROLE_ARN and CUSTID:
        try:
            import boto3
            sts    = boto3.client('sts')
            creds  = sts.assume_role(
                RoleArn         = CLI_ROLE_ARN,
                RoleSessionName = f'SMLApp-{CUSTID}',
                ExternalId      = 'decision-machine-cli',
                Tags            = [{'Key': 'custid', 'Value': CUSTID}],
            )['Credentials']
            _boto_session = boto3.Session(
                aws_access_key_id     = creds['AccessKeyId'],
                aws_secret_access_key = creds['SecretAccessKey'],
                aws_session_token     = creds['SessionToken'],
            )
            logging.info(f'boto3: assumed role {CLI_ROLE_ARN} with custid={CUSTID}')
        except Exception as e:
            logging.warning(f'boto3: role assumption failed, using ambient credentials: {e}')
            import boto3
            _boto_session = boto3.Session()
    else:
        import boto3
        _boto_session = boto3.Session()
    return _boto_session

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

import sys

# When frozen by PyInstaller, launcher.py has already chdir'd to the .exe
# directory. Resolve BASE from cwd so data/, input/, jobs/ land next to the exe.
BASE  = Path(os.getcwd()) if getattr(sys, 'frozen', False) else Path(__file__).parent
DATA    = BASE / 'data'
INPUT   = BASE / 'input'
JOBS    = BASE / 'jobs'
ARCHIVE = BASE / 'archive'
ERRORS  = BASE / 'errors'
for d in (DATA, INPUT, JOBS, ARCHIVE, ERRORS): d.mkdir(exist_ok=True)

VIEWS_FILE   = DATA / 'views.json'


def read_json(path, default):
    try: return json.loads(path.read_text()) if path.exists() else default
    except: return default

def write_json(path, obj):
    path.write_text(json.dumps(obj, indent=2))

def now(): return datetime.now(timezone.utc).isoformat()
def find(lst, id_): return next((x for x in lst if x.get('id') == id_), None)

app = Flask(__name__)

# ── Static ────────────────────────────────────────────
@app.route('/')
def root(): return send_from_directory(BASE, 'index.html')

@app.route('/<page>.html')
def pages(page):
    allowed = {'index', 'measurements', 'job-plot', 'pdfs-table'}
    if page in allowed: return send_from_directory(BASE, f'{page}.html')
    abort(404)

# ── Config endpoint ───────────────────────────────────
@app.route('/api/config')
def get_config():
    return jsonify({'custid': CUSTID, 'auth_mode': AUTH_MODE, 'email': EMAIL})

# ── TSU ───────────────────────────────────────────────
TSU_BALANCES_TABLE = 'tsu_balances'

def get_tsu_balance_from_dynamo():
    """Read current TSU balance from DynamoDB. Returns int or None."""
    if not CUSTID:
        return None
    try:
        dynamodb = get_boto_session().resource('dynamodb', region_name='us-east-1')
        response = dynamodb.Table(TSU_BALANCES_TABLE).get_item(Key={'custid': CUSTID})
        item     = response.get('Item')
        if item and 'balance' in item:
            return int(item['balance'])
        return None
    except Exception as e:
        logging.warning(f'TSU balance lookup failed: {e}')
        return None

@app.route('/api/tsu/balance')
def get_tsu_balance():
    balance = get_tsu_balance_from_dynamo()
    return jsonify({'balance': balance, 'email': EMAIL})

@app.route('/api/tsu/request', methods=['POST'])
def request_tsu():
    """Upload a TSU request file to S3 OnDemand/ to trigger Stripe invoice."""
    b        = request.get_json(force=True)
    quantity = int(b.get('quantity', 0))
    if quantity <= 0: abort(400, 'quantity must be positive')
    if not CUSTID:    abort(400, 'custid not configured')
    if not EMAIL:     abort(400, 'email not configured in sml-app.config')
    timestr  = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    filename = f'TSURequest_{CUSTID}_{timestr}.txt'
    content  = (
        f'[Request]\n'
        f'custid   = {CUSTID}\n'
        f'quantity = {quantity}\n'
        f'email    = {EMAIL}\n\n'
        f'[Billing]\n'
        f'; Additional Stripe fields to be added\n'
    )
    try:
        s3 = get_boto_session().client('s3')
        s3.put_object(
            Bucket=INPUT_BUCKET,
            Key=f'OnDemand/{filename}',
            Body=content.encode('utf-8'),
            ACL='bucket-owner-full-control'
        )
    except Exception as e:
        abort(500, f'S3 upload failed: {str(e)}')
    return jsonify({'filename': filename, 'quantity': quantity, 'email': EMAIL}), 200


@app.route('/api/views', methods=['GET'])
def get_views():
    views = read_json(VIEWS_FILE, [])
    # Backfill slug for views created before slug field was added
    changed = False
    for v in views:
        if not v.get('slug'):
            v['slug'] = re.sub(r'[^a-z0-9]', '', v.get('title','').lower())
            changed = True
    if changed:
        write_json(VIEWS_FILE, views)
    return jsonify(views)

@app.route('/api/views', methods=['POST'])
def create_view():
    b = request.get_json(force=True)
    title, category = b.get('title','').strip(), b.get('category','').strip()
    if not title or not category: abort(400)
    slug = b.get('slug','').strip() or re.sub(r'[^a-z0-9]', '', title.lower())
    views = read_json(VIEWS_FILE, [])
    v = {'id':'view-'+uuid.uuid4().hex[:10], 'title':title, 'slug':slug,
         'category':category, 'notes':b.get('notes','').strip(),
         'createdAt':now(), 'series':[]}
    views.insert(0, v); write_json(VIEWS_FILE, views)
    return jsonify(v), 201

@app.route('/api/views/<vid>', methods=['PUT'])
def update_view(vid):
    b = request.get_json(force=True)
    views = read_json(VIEWS_FILE, [])
    v = find(views, vid)
    if not v: abort(404)
    for f in ('title','category','notes','slug'): 
        if f in b: v[f] = b[f].strip()
    # Re-derive slug if title changed but no explicit slug provided
    if 'title' in b and 'slug' not in b:
        v['slug'] = re.sub(r'[^a-z0-9]', '', v['title'].lower())
    write_json(VIEWS_FILE, views); return jsonify(v)

@app.route('/api/views/<vid>', methods=['DELETE'])
def delete_view(vid):
    views = read_json(VIEWS_FILE, [])
    v = find(views, vid)
    if not v: abort(404)
    for s in v.get('series',[]): 
        (INPUT / s.get('filename','')).unlink(missing_ok=True)
    write_json(VIEWS_FILE, [x for x in views if x['id'] != vid])
    return '', 204

# ── Series ────────────────────────────────────────────
@app.route('/api/views/<vid>/series', methods=['POST'])
def upload_series(vid):
    views = read_json(VIEWS_FILE, [])
    v = find(views, vid)
    if not v: abort(404)
    if 'file' not in request.files: abort(400)
    f = request.files['file']
    header = request.form.get('header','').strip()
    stored = uuid.uuid4().hex[:8] + '_' + Path(f.filename).name
    dest = INPUT / stored; f.save(str(dest))
    try: meta = parse_csv_meta(dest)
    except ValueError as e: dest.unlink(missing_ok=True); abort(400, str(e))
    if not header: header = ','.join(meta['headers'][1:])
    s = {'id':'series-'+uuid.uuid4().hex[:10], 'name':Path(f.filename).name,
         'filename':stored, 'header':header, 'uploadedAt':now(),
         'headers':meta['headers'], 'dateRange':meta['dateRange'],
         'length':meta['length']}
    v['series'].append(s); write_json(VIEWS_FILE, views)
    return jsonify(s), 201

@app.route('/api/views/<vid>/series/<sid>', methods=['DELETE'])
def delete_series(vid, sid):
    views = read_json(VIEWS_FILE, [])
    v = find(views, vid)
    if not v: abort(404)
    s = find(v['series'], sid)
    if not s: abort(404)
    (INPUT / s.get('filename','')).unlink(missing_ok=True)
    v['series'] = [x for x in v['series'] if x['id'] != sid]
    write_json(VIEWS_FILE, views); return '', 204

@app.route('/api/views/<vid>/series/<sid>/csv')
def get_series_csv(vid, sid):
    views = read_json(VIEWS_FILE, [])
    v = find(views, vid)
    if not v: abort(404)
    s = find(v['series'], sid)
    if not s: abort(404)
    p = INPUT / s['filename']
    if not p.exists(): abort(404)
    return send_file(str(p), mimetype='text/csv')

# ── Job files (direct directory scan) ────────────────
def parse_jobfile(filename):
    """Parse output filenames: {custid}_{slug}_{process}_{descriptor}.csv
    Returns dict with custid, slug, process, descriptor, is_pdfs, is_sml, parsed.
    """
    stem  = Path(filename).stem
    parts = stem.split('_')
    if len(parts) >= 3:
        custid     = parts[0]
        slug       = parts[1]
        process    = parts[2]
        descriptor = '_'.join(parts[3:]) if len(parts) > 3 else ''
        parsed     = True
    else:
        custid = slug = process = descriptor = ''
        parsed = False
    return {
        'custid':     custid,
        'slug':       slug,
        'process':    process,
        'descriptor': descriptor,
        'is_pdfs':    process == 'pdfs',
        'is_sml':     process not in ('pdfs', '', 'unknown'),
        'parsed':     parsed,
    }

@app.route('/api/jobfiles')
def list_jobfiles():
    views     = read_json(VIEWS_FILE, [])
    # Backfill: views created before slug field use title-derived slug
    slug_map  = {}
    for v in views:
        slug = v.get('slug','').strip()
        if not slug:
            slug = re.sub(r'[^a-z0-9]', '', v.get('title','').lower())
        if slug:
            slug_map[slug] = v['id']
    files = []
    for p in sorted(JOBS.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_file() and p.suffix.lower() == '.csv':
            info = parse_jobfile(p.name)
            files.append({
                'filename':      p.name,
                'is_pdfs':       info['is_pdfs'],
                'is_sml':        info['is_sml'],
                'parsed':        info['parsed'],
                'slug':          info['slug'],
                'process':       info['process'],
                'descriptor':    info['descriptor'],
                'custid':        info['custid'],
                'view_id':       slug_map.get(info['slug'], None),
                'modifiedAt':    datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()
            })
    return jsonify(files)

# Headers injected at serve time — Lambda output files have no header row
JOBFILE_HEADERS = {
    'binary': 'Symbol,TS,value,p+,p-,energy,power,resistance,noise,T,FE,therm_p+,therm_p-',
    'units':  'Symbol,TS,value,p,E,T,T_B,exp_n,exp_strain,exp_demand,sus_n,sus_strain,sus_E,sus_demand,var_n,var_strain,var_E,var_del_n,cov_n_strain',
}

@app.route('/api/jobfiles/<filename>/csv')
def get_jobfile_csv(filename):
    if '/' in filename or '\\' in filename or '..' in filename: abort(400)
    p = JOBS / filename
    if not p.exists() or not p.is_file(): abort(404)
    info   = parse_jobfile(filename)
    header = JOBFILE_HEADERS.get(info['process'])
    if header:
        raw  = p.read_bytes()
        data = (header + '\n').encode('utf-8') + raw
        return data, 200, {'Content-Type': 'text/csv'}
    return send_file(str(p), mimetype='text/csv')

@app.route('/api/jobfiles/<filename>/delete', methods=['POST'])
def delete_jobfile(filename):
    if '/' in filename or '\\' in filename or '..' in filename: abort(400)
    p = JOBS / filename
    if not p.exists(): abort(404)
    p.unlink()
    # Also remove from watch_path so the S3 agent never re-copies it
    if WATCH_PATH:
        wp = Path(WATCH_PATH) / filename
        wp.unlink(missing_ok=True)
    return '', 204

@app.route('/api/jobfiles/<filename>/archive', methods=['POST'])
def archive_jobfile(filename):
    if '/' in filename or '\\' in filename or '..' in filename: abort(400)
    src = JOBS / filename
    if not src.exists(): abort(404)
    shutil.move(str(src), str(ARCHIVE / filename))
    if WATCH_PATH:
        (Path(WATCH_PATH) / filename).unlink(missing_ok=True)
    return '', 204

@app.route('/api/jobfiles/archive-all', methods=['POST'])
def archive_all_jobfiles():
    b = request.get_json(force=True)
    filenames = b.get('filenames', [])
    for filename in filenames:
        if '/' in filename or '\\' in filename or '..' in filename: continue
        src = JOBS / filename
        if src.exists():
            shutil.move(str(src), str(ARCHIVE / filename))
        if WATCH_PATH:
            (Path(WATCH_PATH) / filename).unlink(missing_ok=True)
    return '', 204

@app.route('/api/archivedfiles')
def list_archivedfiles():
    views    = read_json(VIEWS_FILE, [])
    slug_map = {}
    for v in views:
        slug = v.get('slug','').strip()
        if not slug:
            slug = re.sub(r'[^a-z0-9]', '', v.get('title','').lower())
        if slug:
            slug_map[slug] = v['id']
    files = []
    for p in sorted(ARCHIVE.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_file() and p.suffix.lower() == '.csv':
            info = parse_jobfile(p.name)
            files.append({
                'filename':   p.name,
                'is_pdfs':    info['is_pdfs'],
                'is_sml':     info['is_sml'],
                'parsed':     info['parsed'],
                'slug':       info['slug'],
                'process':    info['process'],
                'descriptor': info['descriptor'],
                'custid':     info['custid'],
                'view_id':    slug_map.get(info['slug'], None),
                'modifiedAt': datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()
            })
    return jsonify(files)

@app.route('/api/archivedfiles/<filename>/delete', methods=['POST'])
def delete_archivedfile(filename):
    if '/' in filename or '\\' in filename or '..' in filename: abort(400)
    p = ARCHIVE / filename
    if not p.exists(): abort(404)
    p.unlink()
    return '', 204

@app.route('/api/archivedfiles/<filename>/restore', methods=['POST'])
def restore_jobfile(filename):
    if '/' in filename or '\\' in filename or '..' in filename: abort(400)
    src = ARCHIVE / filename
    if not src.exists(): abort(404)
    shutil.move(str(src), str(JOBS / filename))
    return '', 204

# ── Error files ───────────────────────────────────────
@app.route('/api/errorfiles')
def list_errorfiles():
    files = []
    for p in sorted(ERRORS.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_file() and p.suffix.lower() == '.txt' and p.name.startswith('ERROR_'):
            try:
                message = p.read_text(encoding='utf-8').strip()
            except Exception:
                message = ''
            files.append({
                'filename':   p.name,
                'message':    message,
                'modifiedAt': datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()
            })
    return jsonify(files)

@app.route('/api/errorfiles/<filename>/delete', methods=['POST'])
def delete_errorfile(filename):
    if '/' in filename or '\\' in filename or '..' in filename: abort(400)
    p = ERRORS / filename
    if not p.exists(): abort(404)
    p.unlink()
    return '', 204


    if '/' in filename or '\\' in filename or '..' in filename: abort(400)
    p = JOBS / filename
    if not p.exists() or not p.is_file(): abort(404)
    return send_file(str(p), mimetype='text/csv')

# ── CSV validation ────────────────────────────────────
def parse_csv_meta(path):
    DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
    with open(path, newline='', encoding='utf-8-sig') as fh:
        rows = [r for r in csv.reader(fh) if any(c.strip() for c in r)]
    if len(rows) < 2: raise ValueError('Need header + at least one data row.')
    headers = [h.strip() for h in rows[0]]
    while headers and not headers[-1]: headers.pop()
    if len(headers) < 2: raise ValueError('Need at least date + one numeric column.')
    dates = []
    for i, row in enumerate(rows[1:], 2):
        d = row[0].strip() if row else ''
        if not DATE_RE.match(d): raise ValueError(f'Row {i}: bad date "{d}".')
        dates.append(d)
    return {'headers':headers, 'dateRange':[dates[0],dates[-1]], 'length':len(dates)}


# ── SML Job Submit ────────────────────────────────────────
@app.route('/api/submit-sml', methods=['POST'])
def submit_sml():
    """
    Accepts a job submission from the dashboard.
    Reads the data file from input/, generates the .ini, uploads both to S3.
    Data file first, .ini second (triggers Lambda).
    CLI mode only for now — boto3 uses ambient AWS credentials.
    """
    try:
        pass  # boto3 available via get_boto_session()
    except ImportError:
        abort(500, 'boto3 not installed. Run: pip install boto3')

    b = request.get_json(force=True)
    series_filename = b.get('series_filename', '').strip()
    descriptor      = b.get('descriptor', '').strip()
    process         = b.get('process', '').strip()
    measurements    = b.get('measurements', '')
    slug            = b.get('slug', '').strip()
    custid          = CUSTID or b.get('custid', '').strip()

    if not series_filename: abort(400, 'series_filename required')
    if not descriptor:      abort(400, 'descriptor required')
    if process not in ('binary', 'units'): abort(400, 'process must be binary or units')
    if not custid:          abort(400, 'custid not configured')

    # TSU balance guards
    balance = get_tsu_balance_from_dynamo()
    if balance is None:
        abort(402, 'No TSUs funded. Request TSUs before submitting jobs.')
    if balance < -100:
        abort(402, f'Account overdrawn by {abs(balance)} TSUs. Contact support to restore access.')

    # Locate data file
    data_path = None
    views = read_json(VIEWS_FILE, [])
    for v in views:
        for s in v.get('series', []):
            if s.get('filename') == series_filename:
                data_path = INPUT / series_filename
                break

    if not data_path or not data_path.exists():
        abort(404, f'Data file not found: {series_filename}')

    data_filename = f'{descriptor}.csv'

    # Build .ini content
    file_output = f'{OUTPUT_BUCKET}/{custid}'
    user_output = f'{custid}_{slug}_{process}' if slug else f'{custid}_{process}'
    if process == 'binary':
        ini_filename = f'config_{descriptor}.ini'
        ini_content  = (
            f'[Default]\n'
            f'FileOutput = {file_output}\n'
            f'Measurements = {measurements}\n'
            f'Crumbs = {user_output}\n\n'
        )
    else:  # units
        ini_filename = f'measure_{descriptor}.ini'
        ini_content  = (
            f'[Default]\n'
            f'FileOutput = {file_output}\n'
            f'Measurements = {measurements}\n'
            f'Crumbs = {user_output}\n\n'
        )

    # Upload to S3 — data file first (header stripped), .ini second
    try:
        s3 = get_boto_session().client('s3')

        # Step 1: data file — read local file, strip header row, upload
        with open(data_path, 'rb') as fh:
            raw = fh.read()
        # Strip BOM if present
        if raw[:3] == b'\xef\xbb\xbf':
            raw = raw[3:]
        # Drop first line (header)
        first_newline = raw.find(b'\n')
        data_no_header = raw[first_newline + 1:] if first_newline != -1 else raw

        s3.put_object(
            Bucket=INPUT_BUCKET,
            Key=f'OnDemand/{data_filename}',
            Body=data_no_header,
            ACL='bucket-owner-full-control'
        )

        # Step 2: .ini file (triggers Lambda)
        s3.put_object(
            Bucket=INPUT_BUCKET,
            Key=f'OnDemand/{ini_filename}',
            Body=ini_content.encode('utf-8'),
            ACL='bucket-owner-full-control'
        )

    except Exception as e:
        abort(500, f'S3 upload failed: {str(e)}')

    return jsonify({
        'data_file': data_filename,
        'ini_file':  ini_filename,
        'bucket':    INPUT_BUCKET,
    }), 200



# ── S3 Download Agent ─────────────────────────────────
def s3_download_agent(downloaded_this_session):
    """
    Background thread. Polls output S3 bucket for new files belonging to this
    custid and downloads them to WATCH_PATH. Only downloads files not already
    present on disk. Runs every AGENT_INTERVAL seconds.
    CLI mode: boto3 uses ambient ~/.aws/credentials.
    """
    if not CUSTID:
        logging.warning('S3 agent: custid not configured — agent disabled.')
        return
    if not WATCH_PATH:
        logging.warning('S3 agent: watch_path not configured — agent disabled.')
        return

    watch_dir = Path(WATCH_PATH)
    try:
        watch_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logging.error(f'S3 agent: cannot create watch_path {watch_dir}: {e}')
        return

    logging.info(f'S3 agent started — polling s3://{OUTPUT_BUCKET}/{CUSTID}/ every {AGENT_INTERVAL}s')

    while True:
        try:
            s3 = get_boto_session().client('s3')
            prefix = f'{CUSTID}/'

            # List all objects in custid prefix
            paginator = s3.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=OUTPUT_BUCKET, Prefix=prefix)

            # What's already on disk
            existing_csv = {f.name for f in watch_dir.iterdir() if f.suffix.lower() == '.csv'}
            errors_dir   = watch_dir / 'errors'
            errors_dir.mkdir(exist_ok=True)
            existing_err = {f.name for f in errors_dir.iterdir() if f.suffix.lower() == '.txt'}

            downloaded = 0
            for page in pages:
                for obj in page.get('Contents', []):
                    key      = obj['Key']
                    filename = Path(key).name

                    # Error files — ERROR_*.txt
                    if filename.startswith('ERROR_') and filename.endswith('.txt'):
                        if filename in existing_err:
                            continue
                        dest = errors_dir / filename
                        try:
                            s3.download_file(OUTPUT_BUCKET, key, str(dest))
                            logging.info(f'S3 agent: downloaded error file {filename}')
                            existing_err.add(filename)
                            downloaded_this_session.add('errors/' + filename)
                            downloaded += 1
                        except Exception as e:
                            logging.error(f'S3 agent: failed to download {key}: {e}')
                        continue

                    # Output CSV files
                    if not filename.endswith('.csv'):
                        continue
                    if not filename.startswith(CUSTID + '_'):
                        logging.debug(f'S3 agent: skipping {filename} — custid mismatch')
                        continue
                    if filename in existing_csv:
                        continue
                    dest = watch_dir / filename
                    try:
                        s3.download_file(OUTPUT_BUCKET, key, str(dest))
                        logging.info(f'S3 agent: downloaded {filename}')
                        existing_csv.add(filename)
                        downloaded_this_session.add(filename)
                        downloaded += 1
                    except Exception as e:
                        logging.error(f'S3 agent: failed to download {key}: {e}')

            if downloaded:
                logging.info(f'S3 agent: {downloaded} new file(s) downloaded to {watch_dir}')

        except Exception as e:
            logging.error(f'S3 agent: poll error: {e}')

        time.sleep(AGENT_INTERVAL)


# ── File Watcher ──────────────────────────────────────
def file_watcher(downloaded_this_session):
    """
    Background thread. Polls WATCH_PATH for new files downloaded this session.
    Copies custid_*.csv to jobs/ and ERROR_*.txt to errors/.
    """
    if not WATCH_PATH:
        logging.warning('File watcher: watch_path not configured — watcher disabled.')
        return

    watch_dir = Path(WATCH_PATH)
    logging.info(f'File watcher started — polling {watch_dir} every {WATCH_INTERVAL}s')

    while True:
        try:
            if watch_dir.exists():
                existing_jobs = {f.name for f in JOBS.iterdir()   if f.suffix.lower() == '.csv'}
                existing_errs = {f.name for f in ERRORS.iterdir() if f.suffix.lower() == '.txt'}

                for entry in list(downloaded_this_session):
                    # Error files are prefixed with 'errors/' in the session set
                    if entry.startswith('errors/'):
                        filename = entry[len('errors/'):]
                        src = watch_dir / 'errors' / filename
                        if not src.exists() or filename in existing_errs:
                            continue
                        try:
                            shutil.copy2(str(src), str(ERRORS / filename))
                            logging.info(f'File watcher: copied {filename} → errors/')
                            existing_errs.add(filename)
                        except Exception as e:
                            logging.error(f'File watcher: failed to copy error {filename}: {e}')
                    else:
                        filename = entry
                        src = watch_dir / filename
                        if not src.exists() or filename in existing_jobs:
                            continue
                        try:
                            shutil.copy2(str(src), str(JOBS / filename))
                            logging.info(f'File watcher: copied {filename} → jobs/')
                            existing_jobs.add(filename)
                        except Exception as e:
                            logging.error(f'File watcher: failed to copy {filename}: {e}')
        except Exception as e:
            logging.error(f'File watcher: poll error: {e}')

        time.sleep(WATCH_INTERVAL)



# ── Start background threads ──────────────────────────
if os.environ.get('WERKZEUG_RUN_MAIN') != 'false':
    _downloaded_this_session = set()

    # Pre-populate with any files already in watch_path that haven't
    # made it to jobs/ or errors/ yet — handles Flask crash/restart edge case
    if WATCH_PATH and CUSTID:
        _watch_dir = Path(WATCH_PATH)
        if _watch_dir.exists():
            _existing_jobs    = {f.name for f in JOBS.iterdir()    if f.suffix.lower() == '.csv'}
            _existing_errs    = {f.name for f in ERRORS.iterdir()  if f.suffix.lower() == '.txt'}
            _existing_archive = {f.name for f in ARCHIVE.iterdir() if f.suffix.lower() == '.csv'}
            # Orphaned CSVs — not in jobs/ and not already archived
            for _f in _watch_dir.iterdir():
                if _f.suffix.lower() == '.csv' and _f.name.startswith(CUSTID + '_'):
                    if _f.name not in _existing_jobs and _f.name not in _existing_archive:
                        _downloaded_this_session.add(_f.name)
                        logging.info(f'Startup: queued orphaned file {_f.name}')
            # Orphaned error files
            _errors_dir = _watch_dir / 'errors'
            if _errors_dir.exists():
                for _f in _errors_dir.iterdir():
                    if _f.suffix.lower() == '.txt' and _f.name.startswith('ERROR_'):
                        if _f.name not in _existing_errs:
                            _downloaded_this_session.add('errors/' + _f.name)
                            logging.info(f'Startup: queued orphaned error file {_f.name}')

    threading.Thread(target=s3_download_agent,
                     args=(_downloaded_this_session,),
                     daemon=True, name='s3-agent').start()
    threading.Thread(target=file_watcher,
                     args=(_downloaded_this_session,),
                     daemon=True, name='file-watcher').start()

if __name__ == '__main__':
    print('SML-App running at http://localhost:5000')
    app.run(debug=True, port=5000)