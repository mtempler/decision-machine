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
