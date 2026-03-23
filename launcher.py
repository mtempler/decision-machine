"""
launcher.py — entry point for the SML-App PyInstaller bundle.

Responsibilities:
  1. Resolve the correct base directory whether running frozen (.exe) or as a script
  2. Change working directory so server.py finds its data/, input/, jobs/ folders
     relative to the executable (not a temp extraction dir)
  3. Open the browser after a short delay
  4. Start Flask
"""
import sys
import os
import threading
import webbrowser
import time
from pathlib import Path

# ── Base directory ────────────────────────────────────
# When frozen by PyInstaller, sys.executable is the .exe itself.
# We want data directories (data/, input/, jobs/, downloads/) to live
# next to the .exe — not inside the PyInstaller temp extraction folder.
if getattr(sys, 'frozen', False):
    # Running as bundled .exe
    BASE_DIR = Path(sys.executable).parent
    # PyInstaller extracts bundled files (HTML, config) to sys._MEIPASS
    BUNDLE_DIR = Path(sys._MEIPASS)
else:
    # Running as plain script
    BASE_DIR   = Path(__file__).parent
    BUNDLE_DIR = BASE_DIR

# Change to BASE_DIR so relative paths in server.py resolve correctly
os.chdir(str(BASE_DIR))

# Copy bundled HTML into BASE_DIR on every launch (always up to date)
# Only sml-app.config is preserved if already present (user-personalised)
for filename in ('index.html', 'measurements.html', 'job-plot.html', 'pdfs-table.html'):
    src  = BUNDLE_DIR / filename
    dest = BASE_DIR   / filename
    if src.exists():
        import shutil
        shutil.copy2(str(src), str(dest))

# Config only copied if not already present (personalised per tester)
config_src  = BUNDLE_DIR / 'sml-app.config'
config_dest = BASE_DIR   / 'sml-app.config'
if config_src.exists() and not config_dest.exists():
    import shutil
    shutil.copy2(str(config_src), str(config_dest))

# ── Open browser ──────────────────────────────────────
def open_browser():
    time.sleep(1.5)  # Give Flask time to start
    webbrowser.open('http://localhost:5000')

threading.Thread(target=open_browser, daemon=True).start()

# ── Start Flask ───────────────────────────────────────
# Import server after chdir so all paths resolve correctly
import server
server.app.run(host='127.0.0.1', port=5000, debug=False)
