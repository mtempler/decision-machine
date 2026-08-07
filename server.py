"""
SML-App Flask server
Directory layout:
  server.py
  index.html / measurements.html / job-plot.html / setup.html
  data/views.json   ← auto-created
  data/jobs.json    ← auto-created
  input/            ← uploaded time-series CSVs
  jobs/             ← SML measurement CSVs (dropped here by pipeline)
"""
import sys, json, os, uuid, csv, re, configparser, io, threading, time, shutil, logging, gzip
from datetime import datetime, timezone, timedelta
from pathlib import Path
from flask import Flask, abort, jsonify, request, send_file, send_from_directory

#
# Cognito auth — required. Password is never written to disk; it lives in
# the OS keychain, looked up by (KEYRING_SERVICE, COGNITO_USERNAME) at auth time.
try:
    import keyring          # OS keychain (Windows Credential Manager / macOS Keychain)
except Exception:
    keyring = None
try:
    from pycognito import Cognito
except Exception:
    Cognito = None
try:
    # Raised by Cognito.authenticate() when the user is in FORCE_CHANGE_PASSWORD
    # state (default for admin-created users, and what an admin-triggered mass
    # password reset — e.g. after a security incident — puts existing users into).
    # NOTE: verify this exists in the pinned pycognito version; the except-Exception
    # fallback below still catches the failure, just without the specific handling.
    from pycognito.exceptions import ForceChangePasswordException
except Exception:
    ForceChangePasswordException = None

# ── Config ────────────────────────────────────────────────
_cfg = configparser.ConfigParser()
_cfg_path = Path(os.getcwd()) / 'sml-app.config' if getattr(sys, 'frozen', False) \
            else Path(__file__).parent / 'sml-app.config'
_cfg.read(_cfg_path)

def cfg(section, key, fallback=''):
    return _cfg.get(section, key, fallback=fallback)

CUSTID         = cfg('identity', 'custid',         fallback='')
EMAIL          = cfg('identity', 'email',           fallback='')

# Cognito identity — populated by the setup wizard.
COGNITO_USERNAME  = cfg('identity', 'cognito_username',  fallback='')
COGNITO_REGION    = cfg('identity', 'cognito_region',    fallback='us-east-1')
USER_POOL_ID      = cfg('identity', 'user_pool_id',      fallback='')
CLIENT_ID         = cfg('identity', 'client_id',         fallback='')
IDENTITY_POOL_ID  = cfg('identity', 'identity_pool_id',  fallback='')
KEYRING_SERVICE   = 'SML-App'
INPUT_BUCKET   = cfg('storage',  'input_bucket',   fallback='customer.decision-machine.com')
OUTPUT_BUCKET  = cfg('storage',  'output_bucket',  fallback='output.customer.decision-machine.com')
WATCH_PATH     = cfg('storage',  'watch_path',     fallback='')
WATCH_INTERVAL = int(cfg('storage', 'watch_interval', fallback='30'))
AGENT_INTERVAL = int(cfg('storage', 'agent_interval', fallback='60'))

# ── boto3 session — Cognito federated credentials ─────
# Lazy: no AWS/Cognito call happens at startup. The session is only
# established the first time something actually needs AWS access (TSU
# balance check, job submit, TSU request, or the background S3 agent's
# next poll) — and re-established automatically once the cached
# credentials are within 5 minutes of expiring.
_boto_session  = None
_creds_expiry  = None   # datetime (UTC) the current _boto_session's creds expire
_cognito_obj   = None   # cached pycognito Cognito instance — holds the refresh_token so we
                        # don't have to touch the keychain/password on every renewal
_password_change_required = False  # set when Cognito returns NEW_PASSWORD_REQUIRED —
                                    # e.g. an admin-forced reset (default password, or a
                                    # mass reset after a security incident). Cleared once
                                    # the customer sets a new password via the dashboard.

_EXPIRY_BUFFER = timedelta(minutes=5)


class PasswordChangeRequiredError(RuntimeError):
    """Raised when Cognito reports FORCE_CHANGE_PASSWORD for this user. Resolved via
    POST /api/auth/change-password, not by retrying authentication with the old password."""
    pass


def _cognito_get_password():
    if keyring is None:
        raise RuntimeError('keyring package not installed')
    if not COGNITO_USERNAME:
        raise RuntimeError('cognito_username not set in sml-app.config — run the setup wizard')
    pw = keyring.get_password(KEYRING_SERVICE, COGNITO_USERNAME)
    if not pw:
        raise RuntimeError(
            f'No password found in OS keychain for {COGNITO_USERNAME}. '
            f'Run the setup wizard to store it.'
        )
    return pw


def _cognito_id_token(force_password=False):
    """Return a fresh Cognito ID token, refreshing via refresh_token when possible
    and only falling back to a full SRP (password) login when necessary."""
    global _cognito_obj, _password_change_required
    if Cognito is None:
        raise RuntimeError('pycognito package not installed')
    if not (USER_POOL_ID and CLIENT_ID and COGNITO_USERNAME):
        raise RuntimeError('user_pool_id, client_id, and cognito_username must be set in sml-app.config — run the setup wizard')

    if _cognito_obj is not None and not force_password:
        try:
            _cognito_obj.renew_access_token()
            logging.info('Cognito: renewed tokens via refresh_token')
            return _cognito_obj.id_token
        except Exception as e:
            logging.warning(f'Cognito: refresh_token renewal failed, falling back to password auth: {e}')

    password = _cognito_get_password()
    _cognito_obj = Cognito(
        USER_POOL_ID, CLIENT_ID,
        user_pool_region=COGNITO_REGION,
        username=COGNITO_USERNAME,
    )
    try:
        _cognito_obj.authenticate(password=password)
    except Exception as e:
        is_password_change = (
            (ForceChangePasswordException is not None and isinstance(e, ForceChangePasswordException))
            or 'NEW_PASSWORD_REQUIRED' in str(e)
        )
        if is_password_change:
            _password_change_required = True
            logging.warning(f'Cognito: {COGNITO_USERNAME} must set a new password before authenticating '
                             f'(FORCE_CHANGE_PASSWORD — admin reset or first login with a default password)')
            raise PasswordChangeRequiredError(
                'Cognito requires a new password for this account. Set one from the dashboard.'
            ) from e
        raise
    _password_change_required = False
    logging.info(f'Cognito: authenticated {COGNITO_USERNAME} via SRP')
    return _cognito_obj.id_token


def _cognito_federated_credentials(id_token):
    """Exchange a Cognito User Pool ID token for scoped temporary AWS credentials
    via the Identity Pool. custid-scoping is enforced on the IAM role attached
    to the identity pool's authenticated role via principal tags (ABAC)."""
    import boto3
    if not IDENTITY_POOL_ID:
        raise RuntimeError('identity_pool_id not set in sml-app.config — run the setup wizard')
    logins_key = f'cognito-idp.{COGNITO_REGION}.amazonaws.com/{USER_POOL_ID}'
    ci = boto3.client('cognito-identity', region_name=COGNITO_REGION)
    identity_id = ci.get_id(
        IdentityPoolId=IDENTITY_POOL_ID,
        Logins={logins_key: id_token},
    )['IdentityId']
    creds = ci.get_credentials_for_identity(
        IdentityId=identity_id,
        Logins={logins_key: id_token},
    )['Credentials']
    return creds  # {'AccessKeyId', 'SecretKey', 'SessionToken', 'Expiration'}


def get_boto_session():
    global _boto_session, _creds_expiry
    now_dt = datetime.now(timezone.utc)
    if _boto_session is not None and now_dt < _creds_expiry - _EXPIRY_BUFFER:
        return _boto_session

    try:
        id_token = _cognito_id_token()
        creds    = _cognito_federated_credentials(id_token)
        _boto_session = __import__('boto3').Session(
            aws_access_key_id     = creds['AccessKeyId'],
            aws_secret_access_key = creds['SecretKey'],
            aws_session_token     = creds['SessionToken'],
        )
        _creds_expiry = creds['Expiration']
        logging.info(f'boto3: obtained Cognito-federated credentials for {COGNITO_USERNAME}, '
                     f'expiring {_creds_expiry.isoformat()}')
    except Exception as e:
        logging.error(f'Cognito authentication failed: {e}')
        # Force a clean password re-auth on the next attempt rather than
        # repeatedly trying a possibly-stale refresh_token.
        _cognito_obj_reset()
        raise

    return _boto_session


def _cognito_obj_reset():
    global _cognito_obj
    _cognito_obj = None

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

# Resolve WATCH_PATH against BASE so relative paths always land next to the exe
if WATCH_PATH:
    WATCH_PATH = str(BASE / WATCH_PATH)

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
def root():
    # Local features (Views, measurements, file management) don't need custid/email/Cognito
    # at all, so the dashboard is always the landing page. AWS-touching actions surface
    # their own auth problems when actually attempted — see /api/submit-sml, /api/tsu/*.
    return send_from_directory(BASE, 'index.html')

@app.route('/<page>.html')
def pages(page):
    allowed = {'index', 'measurements', 'job-plot', 'dm-plot', 'pdfs-table', 'setup'}
    if page not in allowed:
        abort(404)
    return send_from_directory(BASE, f'{page}.html')

# ── Cognito region map ────────────────────────────────
# cognito-regions.json ships with the bundle (refreshed every launch, like the
# HTML — see launcher.py) and maps a region code to that region's pool/client/
# identity-pool IDs. The browser only ever sees region codes + display labels;
# /api/setup resolves the actual AWS resource IDs itself, server-side, so a
# customer's form submission can't be tampered with to point at a different
# region's (or another customer's) Cognito resources.
REGIONS_FILE = BASE / 'cognito-regions.json'

def _load_region_map():
    return read_json(REGIONS_FILE, {})

@app.route('/api/regions')
def get_regions():
    """Region codes + labels only — never the underlying pool/client/identity IDs."""
    regions = _load_region_map()
    return jsonify([
        {'code': code, 'label': info.get('label', code)}
        for code, info in regions.items()
    ])

# ── Setup wizard ──────────────────────────────────────
@app.route('/api/setup', methods=['POST'])
def save_setup():
    """Write sml-app.config from first-launch wizard input.
    Password is written to the OS keychain via `keyring` and never touches disk.
    Cognito username is always the account email — no separate field. Pool/client/
    identity-pool IDs are resolved server-side from cognito-regions.json based on
    the region the customer picked from the dropdown; only regions present in that
    file are selectable, so an unsupported region can't be submitted."""
    b               = request.get_json(force=True)
    custid          = b.get('custid', '').strip()
    email           = b.get('email', '').strip()
    region_code     = b.get('region', '').strip()
    password        = b.get('password', '')
    cognito_username = email

    if not custid:      abort(400, 'custid is required')
    if not email:        abort(400, 'email is required')
    if not region_code: abort(400, 'region is required')
    if not password:    abort(400, 'password is required')
    if keyring is None:
        abort(500, 'keyring package not installed on this build — cannot store password securely')

    region_map  = _load_region_map()
    region_info = region_map.get(region_code)
    if not region_info:
        abort(400, f'Unknown region "{region_code}" — cognito-regions.json may be out of date')

    user_pool_id     = region_info.get('user_pool_id', '')
    client_id        = region_info.get('client_id', '')
    identity_pool_id = region_info.get('identity_pool_id', '')
    if not (user_pool_id and client_id and identity_pool_id):
        abort(500, f'cognito-regions.json entry for "{region_code}" is incomplete')

    try:
        keyring.set_password(KEYRING_SERVICE, cognito_username, password)
    except Exception as e:
        abort(500, f'Failed to store password in OS keychain: {e}')
    logging.info(f'Setup wizard: stored Cognito password in OS keychain for {cognito_username}')

    config_content = (
        '[identity]\n'
        f'custid           = {custid}\n'
        f'email            = {email}\n'
        f'cognito_username = {cognito_username}\n'
        f'cognito_region   = {region_code}\n'
        f'user_pool_id     = {user_pool_id}\n'
        f'client_id        = {client_id}\n'
        f'identity_pool_id = {identity_pool_id}\n\n'
        '[storage]\n'
        'input_bucket   = customer.decision-machine.com\n'
        'output_bucket  = output.customer.decision-machine.com\n'
        'watch_path     = downloads\n'
        'watch_interval = 30\n'
        'agent_interval = 60\n'
    )
    try:
        _cfg_path.write_text(config_content, encoding='utf-8')
        logging.info(f'Setup wizard: wrote sml-app.config for custid={custid}')
    except Exception as e:
        abort(500, f'Failed to write sml-app.config: {e}')

    return jsonify({'status': 'ok', 'custid': custid})

# ── Config endpoint ───────────────────────────────────
@app.route('/api/config')
def get_config():
    return jsonify({'custid': CUSTID, 'email': EMAIL})

# ── Auth status ───────────────────────────────────────
# Exercises get_boto_session() (cheap once cached — only does real work when
# credentials are missing/expired) so the dashboard can show a persistent
# warning banner if Cognito auth is failing, rather than only surfacing as
# a confusing null TSU balance or a failed job submit.
@app.route('/api/auth/status')
def get_auth_status():
    try:
        get_boto_session()
        return jsonify({'ok': True, 'password_change_required': False})
    except PasswordChangeRequiredError as e:
        # Don't retry auth here — surface distinctly so the dashboard can show
        # a "set new password" form instead of a generic failure banner.
        return jsonify({'ok': False, 'password_change_required': True, 'error': str(e)})
    except Exception as e:
        logging.error(f'Auth status check failed: {e}')
        return jsonify({'ok': False, 'password_change_required': False, 'error': str(e)})


@app.route('/api/auth/change-password', methods=['POST'])
def change_password():
    """Complete a Cognito FORCE_CHANGE_PASSWORD challenge (admin-set default
    password, or an admin-triggered mass reset — e.g. after a security incident)
    using the customer's own chosen new password. Uses the still-valid old
    password from the keychain — the customer doesn't need to know or re-enter it."""
    global _password_change_required, _boto_session, _creds_expiry

    b = request.get_json(force=True)
    new_password = b.get('new_password', '')
    if not new_password:
        abort(400, 'new_password is required')
    if not (USER_POOL_ID and CLIENT_ID and COGNITO_USERNAME):
        abort(400, 'Cognito is not configured — run the setup wizard')

    old_password = _cognito_get_password()

    import boto3
    idp = boto3.client('cognito-idp', region_name=COGNITO_REGION)
    try:
        resp = idp.initiate_auth(
            ClientId=CLIENT_ID,
            AuthFlow='USER_PASSWORD_AUTH',
            AuthParameters={'USERNAME': COGNITO_USERNAME, 'PASSWORD': old_password},
        )
    except Exception as e:
        abort(502, f'Could not reach Cognito to start the password change: {e}')

    if resp.get('ChallengeName') != 'NEW_PASSWORD_REQUIRED':
        # Nothing pending — the account may have already been changed by another means.
        _password_change_required = False
        abort(409, 'No password change is currently required for this account.')

    try:
        idp.respond_to_auth_challenge(
            ClientId=CLIENT_ID,
            ChallengeName='NEW_PASSWORD_REQUIRED',
            Session=resp['Session'],
            ChallengeResponses={'USERNAME': COGNITO_USERNAME, 'NEW_PASSWORD': new_password},
        )
    except idp.exceptions.InvalidPasswordException as e:
        abort(400, f"Password doesn't meet the account's password policy: {e}")
    except Exception as e:
        abort(500, f'Failed to set new password: {e}')

    # New password is now permanent in Cognito — persist it locally and force
    # a clean re-authentication on the next AWS call.
    try:
        keyring.set_password(KEYRING_SERVICE, COGNITO_USERNAME, new_password)
    except Exception as e:
        # Cognito-side change already succeeded; a keychain write failure here
        # would otherwise leave the app authenticating with a password that no
        # longer works, with no way to recover except re-running setup.
        abort(500, f'Password was changed in Cognito but could not be saved locally: {e}. '
                    f'Re-run the setup wizard with the new password.')

    _password_change_required = False
    _cognito_obj_reset()
    _boto_session = None
    _creds_expiry = None
    logging.info(f'Password changed for {COGNITO_USERNAME}')
    return jsonify({'status': 'ok'})

# ── Trail progress ────────────────────────────────────
TRAIL_PROGRESS_FILE = DATA / 'trail_progress.json'

@app.route('/api/trail', methods=['GET'])
def get_trail():
    """Return current trail progress."""
    return jsonify(read_json(TRAIL_PROGRESS_FILE, {}))

@app.route('/api/trail', methods=['POST'])
def save_trail():
    """Save trail progress."""
    b = request.get_json(force=True)
    write_json(TRAIL_PROGRESS_FILE, b)
    logging.info(f'Trail progress saved: {b}')
    return jsonify({'status': 'ok'})

# ── TSU ───────────────────────────────────────────────
TSU_BALANCES_TABLE = 'tsu_balances'

def get_tsu_balance_from_dynamo():
    """Read current TSU balance from DynamoDB. Returns int or None.
    Lets PasswordChangeRequiredError propagate — the route surfaces that distinctly
    rather than folding it into a plain null balance."""
    if not CUSTID:
        return None
    try:
        dynamodb = get_boto_session().resource('dynamodb', region_name='us-east-1')
        response = dynamodb.Table(TSU_BALANCES_TABLE).get_item(Key={'custid': CUSTID})
        item     = response.get('Item')
        if item and 'balance' in item:
            return int(item['balance'])
        return None
    except PasswordChangeRequiredError:
        raise
    except Exception as e:
        logging.error(f'TSU balance lookup failed: {e}', exc_info=True)
        return None

@app.route('/api/tsu/balance')
def get_tsu_balance():
    try:
        balance = get_tsu_balance_from_dynamo()
        return jsonify({'balance': balance, 'email': EMAIL, 'password_change_required': False})
    except PasswordChangeRequiredError as e:
        # Still 200 — this is a passive display endpoint, not an action. The dashboard
        # shows the password-change prompt only if the customer then tries to act on it.
        return jsonify({'balance': None, 'email': EMAIL, 'password_change_required': True, 'error': str(e)})

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
    except PasswordChangeRequiredError as e:
        return jsonify({'error': str(e), 'password_change_required': True}), 401
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
    resp = jsonify(s)
    if meta.get('warning'):
        resp.headers['X-Warning'] = meta['warning']
    return resp, 201

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
        'is_pdfs':    process in ('pdfs', 'interaction', 'corr', 'forecast'),
        'is_sml':     process not in ('pdfs', 'interaction', 'corr', 'forecast', '', 'unknown'),
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
    'units':  'Symbol,TS,value,momentum,energy,free energy,free entropy,temperature,expected demand,expected E,delta demand',
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

@app.route('/api/jobfiles/<filename>/meta')
def serve_jobfile_meta(filename):
    """Serve companion .meta file for a job CSV — key=value plot overlay data."""
    if any(c in filename for c in ('/', '\\', '..')):
        abort(400, 'invalid filename')
    meta_name = re.sub(r'\.csv$', '.meta', filename, flags=re.IGNORECASE)
    if not meta_name.endswith('.meta'):
        meta_name = filename + '.meta'
    p = JOBS / meta_name
    if not p.exists():
        abort(404, f'Meta file not found: {meta_name}')
    return send_from_directory(str(JOBS), meta_name, mimetype='text/plain')


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
    # Compressed files are archival records — cannot be restored
    if (ARCHIVE / (filename + '.gz')).exists():
        abort(409, 'This file has been compressed for archival and cannot be restored.')
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

@app.route('/api/errorfiles/dismiss-all', methods=['POST'])
def dismiss_all_errorfiles():
    for p in ERRORS.iterdir():
        if p.is_file() and p.suffix.lower() == '.txt' and p.name.startswith('ERROR_'):
            p.unlink(missing_ok=True)
    return '', 204

# ── CSV validation ────────────────────────────────────
_INVALID_VALUES  = {'nan', 'inf', '-inf', '+inf'}
_VALID_HEADER_RE = re.compile(r'^[A-Za-z0-9_ \-\.]+$')

def parse_csv_meta(path):
    DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
    with open(path, newline='', encoding='utf-8-sig') as fh:
        rows = [r for r in csv.reader(fh) if any(c.strip() for c in r)]
    if len(rows) < 2: raise ValueError('Need header + at least one data row.')
    headers = [h.strip() for h in rows[0]]
    while headers and not headers[-1]: headers.pop()
    if len(headers) < 2: raise ValueError('Need at least date + one numeric column.')
    # Validate header characters (skip col 0 — date column)
    for h in headers[1:]:
        if not _VALID_HEADER_RE.match(h):
            bad_chars = ', '.join(sorted({c for c in h if not re.match(r'[A-Za-z0-9_ \-\.]', c)}))
            raise ValueError(f'Column header "{h}" contains invalid characters: {bad_chars}. '
                             f'Headers may only contain letters, numbers, spaces, hyphens, underscores, and dots.')
    dates = []
    for i, row in enumerate(rows[1:], 2):
        d = row[0].strip() if row else ''
        if not DATE_RE.match(d): raise ValueError(f'Row {i}: bad date "{d}".')
        dates.append(d)
        # Check numeric columns for NaN, inf, and empty cells
        for j, col_name in enumerate(headers[1:], 1):
            val = row[j].strip() if j < len(row) else ''
            if val == '':
                raise ValueError(f'Row {i}, column "{col_name}": empty cell. Fill or remove before uploading.')
            if val.lower() in _INVALID_VALUES:
                raise ValueError(f'Row {i}, column "{col_name}": invalid value "{val}". Fix or remove before uploading.')
    length = len(dates)
    warning = ''
    if length < 30:
        warning = f'This series has {length} timestamps. Units process requires a minimum of 30 for reliable results.'
    elif length < 100:
        warning = f'This series has {length} timestamps. Binary process requires a minimum of 100 for reliable results.'
    return {'headers': headers, 'dateRange': [dates[0], dates[-1]], 'length': length, 'warning': warning}


# ── SML Job Submit ────────────────────────────────────────
@app.route('/api/submit-sml', methods=['POST'])
def submit_sml():
    """
    Accepts a job submission from the dashboard.
    Reads the data file from input/, generates the .ini, uploads both to S3.
    Data file first, .ini second (triggers Lambda).
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
    if process not in ('binary', 'units', 'pdfs', 'interaction', 'corr', 'forecast'):
        abort(400, 'process must be binary, units, pdfs, interaction, corr, or forecast')
    if not custid:          abort(400, 'custid not configured')

    # TSU balance guards
    try:
        balance = get_tsu_balance_from_dynamo()
    except PasswordChangeRequiredError as e:
        return jsonify({'error': str(e), 'password_change_required': True}), 401
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

    horizon      = b.get('horizon', 18)

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
    elif process == 'units':
        ini_filename = f'measure_{descriptor}.ini'
        ini_content  = (
            f'[Default]\n'
            f'FileOutput = {file_output}\n'
            f'Measurements = {measurements}\n'
            f'Crumbs = {user_output}\n\n'
        )
    elif process == 'pdfs':
        ini_filename = f'pdfs_{descriptor}.ini'
        ini_content  = (
            f'[Default]\n'
            f'FileOutput = {file_output}\n'
            f'Measurements = {measurements}\n'
            f'Crumbs = {user_output}\n'
            f'ConfidenceLevel = 0.95\n\n'
        )
    elif process == 'interaction':
        ini_filename = f'interaction_{descriptor}.ini'
        ini_content  = (
            f'[Default]\n'
            f'FileOutput = {file_output}\n'
            f'Measurements = {measurements}\n'
            f'Crumbs = {user_output}\n\n'
        )
    elif process == 'corr':
        ini_filename = f'corr_{descriptor}.ini'
        ini_content  = (
            f'[Default]\n'
            f'FileOutput = {file_output}\n'
            f'Measurements = {measurements}\n'
            f'Crumbs = {user_output}\n\n'
        )
    else:  # forecast
        ini_filename = f'forecast_{descriptor}.ini'
        ini_content  = (
            f'[Default]\n'
            f'FileOutput = {file_output}\n'
            f'Measurements = {measurements}\n'
            f'Crumbs = {user_output}\n'
            f'Horizon = {horizon}\n\n'
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

    except PasswordChangeRequiredError as e:
        return jsonify({'error': str(e), 'password_change_required': True}), 401
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
                            try:
                                s3.delete_object(Bucket=OUTPUT_BUCKET, Key=key)
                                logging.info(f'S3 agent: deleted {key} from S3')
                            except Exception as de:
                                logging.warning(f'S3 agent: downloaded {filename} but could not delete from S3: {de}')
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
                        try:
                            s3.delete_object(Bucket=OUTPUT_BUCKET, Key=key)
                            logging.info(f'S3 agent: deleted {key} from S3')
                        except Exception as de:
                            logging.warning(f'S3 agent: downloaded {filename} but could not delete from S3: {de}')
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
                            src.unlink(missing_ok=True)
                            logging.info(f'File watcher: deleted {filename} from downloads/errors/')
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
                            src.unlink(missing_ok=True)
                            logging.info(f'File watcher: deleted {filename} from downloads/')
                        except Exception as e:
                            logging.error(f'File watcher: failed to copy {filename}: {e}')
        except Exception as e:
            logging.error(f'File watcher: poll error: {e}')

        time.sleep(WATCH_INTERVAL)


# ── Archive Compression Sweep ─────────────────────────
ARCHIVE_COMPRESS_DAYS = 14

def compress_old_archives():
    """Compress .csv files in archive/ older than 14 days to .csv.gz in place."""
    cutoff = time.time() - (ARCHIVE_COMPRESS_DAYS * 86400)
    compressed = 0
    for p in list(ARCHIVE.iterdir()):
        if not p.is_file() or p.suffix.lower() != '.csv':
            continue
        if p.stat().st_mtime > cutoff:
            continue
        gz_path = p.with_suffix('.csv.gz')
        try:
            with open(p, 'rb') as f_in, gzip.open(gz_path, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
            p.unlink()
            compressed += 1
            logging.info(f'Archive sweep: compressed {p.name} → {gz_path.name}')
        except Exception as e:
            logging.error(f'Archive sweep: failed to compress {p.name}: {e}')
            gz_path.unlink(missing_ok=True)
    if compressed:
        logging.info(f'Archive sweep: {compressed} file(s) compressed')

def archive_compression_thread():
    """Daily background sweep to compress old archive files."""
    while True:
        time.sleep(86400)
        compress_old_archives()


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

    # Compress archive files older than 14 days — run once at startup then daily
    compress_old_archives()
    threading.Thread(target=archive_compression_thread,
                     daemon=True, name='archive-compressor').start()

if __name__ == '__main__':
    print('SML-App running at http://localhost:5000')
    app.run(debug=True, port=5000)
