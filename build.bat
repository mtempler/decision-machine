@echo off
:: build.bat — Personalised PyInstaller build for one tester
:: Usage: build.bat <custid> <email>
:: Example: build.bat tGwuZQqEcx mtempler@gmail.com
::
:: Prerequisites (run once):
::   pip install pyinstaller boto3 flask pycognito

setlocal

if "%~1"=="" (
    echo Usage: build.bat ^<custid^> ^<cognito_username^>
    echo Example: build.bat tGwuZQqEcx tester1@example.com
    exit /b 1
)

set CUSTID=%~1
set EMAIL=%~2

echo.
echo Building SML-App for custid: %CUSTID%
if not "%EMAIL%"=="" echo Email: %EMAIL%
echo.

:: ── Write personalised config ──────────────────────────

(
echo [identity]
echo custid            = %CUSTID%
echo auth_mode         = cli
echo email             = %EMAIL%
echo cli_role_arn      = arn:aws:iam::741600857758:role/SMLAppCLIUser
echo.
echo [storage]
echo input_bucket   = customer.decision-machine.com
echo output_bucket  = output.customer.decision-machine.com
echo watch_path     = downloads
echo watch_interval = 30
echo agent_interval = 60
echo.
echo [server]
echo port = 5000
) > sml-app.config

echo Config written.

:: ── Run PyInstaller ────────────────────────────────────
python -m PyInstaller sml-app.spec --noconfirm --clean

if errorlevel 1 (
    echo.
    echo BUILD FAILED. Check output above.
    exit /b 1
)

:: ── Package into a tester ZIP ─────────────────────────
set DIST_DIR=dist\SML-App-%CUSTID%
mkdir "%DIST_DIR%" 2>nul
copy dist\SML-App.exe "%DIST_DIR%\SML-App.exe"

:: Include a README for the tester
(
echo SML-App — %CUSTID%
echo.
echo 1. Double-click SML-App.exe to launch
echo 2. Your browser will open automatically at http://localhost:5000
echo 3. A console window shows status — keep it open while using the app
echo 4. Close the console window to quit
) > "%DIST_DIR%\README.txt"

:: Zip using PowerShell (available on all modern Windows)
powershell -Command "Compress-Archive -Path '%DIST_DIR%\*' -DestinationPath 'dist\SML-App-%CUSTID%.zip' -Force"

echo.
echo ── Build complete ─────────────────────────────────
echo Executable : dist\SML-App.exe
echo Tester ZIP : dist\SML-App-%CUSTID%.zip
echo.

endlocal
