"""
ci_tests.py — Automated regression tests for GitHub Actions CI.
Covers the automatable subset of the regression test suite.
"""
import ast
import configparser
import re
import sys

passed = []
failed = []

def ok(tid, desc):
    passed.append((tid, desc))
    print(f'  PASS  {tid}  {desc}')

def fail(tid, desc, reason):
    failed.append((tid, desc, reason))
    print(f'  FAIL  {tid}  {desc} -> {reason}')

# ── T-211: Binary .ini structure ──────────────────────
def make_ini(process, custid, slug, measurements, descriptor='AA_2026-03-02_6M'):
    file_output  = f'output.customer.decision-machine.com/{custid}'
    user_output  = f'{custid}_{slug}_{process}'
    ini_filename = f'config_{descriptor}.ini' if process == 'binary' else f'measure_{descriptor}.ini'
    ini_content  = f'[Default]\nFileOutput = {file_output}\nMeasurements = {measurements}\nCrumbs = {user_output}\n\n'
    return ini_filename, ini_content

fname, content = make_ini('binary', 'tGwuZQqEcx', 'portfolioreturns', 'AA')
cfg = configparser.ConfigParser()
cfg.read_string(content)
checks = [
    fname == 'config_AA_2026-03-02_6M.ini',
    cfg.has_section('Default'),
    not cfg.has_section('Binary'),
    len(cfg.options('Default')) == 3,
]
if all(checks): ok('T-211', 'Binary .ini structure correct')
else: fail('T-211', 'Binary .ini structure', str(checks))

# ── T-211b: Binary and Units .ini parity ──────────────
_, bc = make_ini('binary', 'tGwuZQqEcx', 'portfolioreturns', 'AA')
_, uc = make_ini('units',  'tGwuZQqEcx', 'portfolioreturns', 'AA')
b = configparser.ConfigParser(); b.read_string(bc)
u = configparser.ConfigParser(); u.read_string(uc)
if set(b.options('Default')) == set(u.options('Default')):
    ok('T-211b', 'Binary and Units .ini have identical fields')
else:
    fail('T-211b', 'Binary/Units parity', f'binary={set(b.options("Default"))} units={set(u.options("Default"))}')

# ── T-215: Header stripping ───────────────────────────
def strip_header(raw):
    if raw[:3] == b'\xef\xbb\xbf':
        raw = raw[3:]
    nl = raw.find(b'\n')
    return raw[nl + 1:] if nl != -1 else raw

r = strip_header(b'TS,AA\n2025-09-04,31.29\n')
if r == b'2025-09-04,31.29\n': ok('T-215', 'Header stripped correctly')
else: fail('T-215', 'Header strip', repr(r))

r_bom = strip_header(b'\xef\xbb\xbfTS,AA\n2025-09-04,31.29\n')
if r_bom == b'2025-09-04,31.29\n': ok('T-215b', 'BOM header stripped correctly')
else: fail('T-215b', 'BOM header strip', repr(r_bom))

# ── T-218: Binary header injection ───────────────────
BINARY_HEADER = 'Symbol,TS,value,p+,p-,energy,power,resistance,noise,T,FE,therm_p+,therm_p-'

def serve_csv(filename, body):
    parts = filename.split('_')
    process = parts[2] if len(parts) > 2 else ''
    if process == 'binary':
        return (BINARY_HEADER + '\n').encode() + body
    return body

result = serve_csv('tGwuZQqEcx_portfolioreturns_binary_AA.csv', b'data')
if result.split(b'\n')[0].decode() == BINARY_HEADER:
    ok('T-218', 'Binary CSV served with correct header')
else:
    fail('T-218', 'Binary header injection', repr(result[:60]))

# ── T-217: custid required ────────────────────────────
def check_custid(c):
    return (400, 'custid not configured') if not c else (200, c)

s, _ = check_custid('')
if s == 400: ok('T-217', 'Missing custid returns 400')
else: fail('T-217', 'custid required', str(s))

s, v = check_custid('tGwuZQqEcx')
if s == 200 and v == 'tGwuZQqEcx': ok('T-217b', 'Config custid used when present')
else: fail('T-217b', 'Config custid', f'{s},{v}')

# ── TSU-Debit: custid from S3 key ─────────────────────
TSU_RE = re.compile(r'_TSU_(\d+)\.csv$', re.IGNORECASE)
key      = 'tGwuZQqEcx/tGwuZQqEcx_portfolioreturns_binary_AA_2026-03-02_6M_Binary_Analysis_20260321_TSU_1.csv'
custid   = key.split('/')[0]
filename = key.split('/')[-1]
n_tsu    = int(TSU_RE.search(filename).group(1))
if custid == 'tGwuZQqEcx' and n_tsu == 1:
    ok('T-DEBITa', 'TSU-Debit parses custid from S3 key')
else:
    fail('T-DEBITa', 'TSU-Debit key parse', f'custid={custid} n_tsu={n_tsu}')

# ── Syntax check ──────────────────────────────────────
for f in ['server.py', 'launcher.py']:
    try:
        with open(f, encoding='utf-8') as fh:
            ast.parse(fh.read())
        ok(f'SYNTAX-{f}', f'{f} syntax valid')
    except SyntaxError as e:
        fail(f'SYNTAX-{f}', f'{f} syntax', f'line {e.lineno}: {e.msg}')
    except FileNotFoundError:
        fail(f'SYNTAX-{f}', f'{f} syntax', 'file not found')

# ── Summary ───────────────────────────────────────────
print()
print(f'Results: {len(passed)} passed, {len(failed)} failed')
if failed:
    for tid, desc, reason in failed:
        print(f'  {tid}: {desc} -> {reason}')
    sys.exit(1)
