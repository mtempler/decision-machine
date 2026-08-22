# sml-app.spec
# PyInstaller spec for SML-App
# Run from the SML-App directory:  pyinstaller sml-app.spec

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['launcher.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        # Bundle all HTML files
        ('index.html',        '.'),
        ('measurements.html', '.'),
        ('job-plot.html',     '.'),
        ('pdfs-table.html',   '.'),
        ('setup.html',        '.'),
    ],
    hiddenimports=[
        # Flask internals
        'flask',
        'flask.templating',
        'werkzeug',
        'werkzeug.serving',
        'werkzeug.routing',
        'werkzeug.middleware.proxy_fix',
        'jinja2',
        'click',
        # boto3 and its dependencies
        'boto3',
        'botocore',
        'botocore.parsers',
        'botocore.serialize',
        'botocore.loaders',
        's3transfer',
        's3transfer.futures',
        # keyring — resolves its actual backend (Windows Credential Manager here)
        # dynamically at runtime via importlib/entry points, which PyInstaller's
        # static analysis can't see through on its own. Without these, `import
        # keyring` can fail silently inside the frozen exe even though it's
        # correctly installed in the build environment — server.py's try/except
        # around the import then quietly sets keyring = None, and every
        # keychain-backed auth call fails with "keyring package not installed".
        'keyring',
        'keyring.backends',
        'keyring.backends.Windows',
        'keyring.backends.chainer',
        'keyring.credentials',
        'keyring.util',
        'keyring.util.platform_',
        # pycognito — SRP auth internals
        'pycognito',
        'pycognito.aws_srp',
        'pycognito.exceptions',
        'pycognito.utils',
        # stdlib used by server.py
        'configparser',
        'threading',
        'shutil',
        'logging',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='SML-App',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,       # Keep console window — useful for seeing agent log lines
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='sml-app.ico',  # Uncomment and add icon file if desired
)
