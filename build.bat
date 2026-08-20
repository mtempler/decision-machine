@echo off
:: build.bat - Personalised PyInstaller build for one tester
:: Usage: build.bat <custid> <email> [region]
:: Example: build.bat tGwuZQqEcx mtempler@gmail.com us-east-1
:: region defaults to us-east-1 if omitted.
::
:: Prerequisites (run once):
::   pip install pyinstaller boto3 flask pycognito keyring

setlocal enabledelayedexpansion

if "%~1"=="" (
    echo Usage: build.bat ^<custid^> ^<email^> [region]
    echo Example: build.bat tGwuZQqEcx tester1@example.com us-east-1
    exit /b 1
)
if "%~2"=="" (
    echo Usage: build.bat ^<custid^> ^<email^> [region]
    echo Example: build.bat tGwuZQqEcx tester1@example.com us-east-1
    exit /b 1
)

set CUSTID=%~1
set EMAIL=%~2
set REGION=%~3
if "%REGION%"=="" set REGION=us-east-1

echo.
echo Building SML-App for custid: %CUSTID%
echo Email:  %EMAIL%
echo Region: %REGION%
echo.

:: -- Resolve Cognito pool/client/identity IDs for this region --
:: cognito-regions.json is the single source of truth for these IDs (also
:: used at runtime by /api/setup) - resolved here at build time instead,
:: per the SML-Training provisioning handoff (see CLAUDE-architecture.md).
set COGNITO_IDS=
for /f "usebackq delims=" %%R in (`powershell -NoProfile -Command "$m = Get-Content 'cognito-regions.json' -Raw | ConvertFrom-Json; $e = $m.'%REGION%'; if (-not $e) { exit 1 }; '{0}|{1}|{2}' -f $e.user_pool_id,$e.client_id,$e.identity_pool_id"`) do set COGNITO_IDS=%%R

if not defined COGNITO_IDS (
    echo BUILD FAILED. Unknown region "%REGION%" in cognito-regions.json.
    exit /b 1
)

for /f "tokens=1,2,3 delims=|" %%A in ("%COGNITO_IDS%") do (
    set USER_POOL_ID=%%A
    set CLIENT_ID=%%B
    set IDENTITY_POOL_ID=%%C
)

:: -- Write personalised config --------------------------

(
echo [identity]
echo custid            = %CUSTID%
echo email             = %EMAIL%
echo cognito_username  = %EMAIL%
echo cognito_region    = %REGION%
echo user_pool_id      = !USER_POOL_ID!
echo client_id         = !CLIENT_ID!
echo identity_pool_id  = !IDENTITY_POOL_ID!
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

echo Config written ^(Cognito user pool: !USER_POOL_ID!^)

:: -- Run PyInstaller ------------------------------------
python -m PyInstaller sml-app.spec --noconfirm --clean

if errorlevel 1 (
    echo.
    echo BUILD FAILED. Check output above.
    exit /b 1
)

:: -- Package into a tester ZIP -------------------------
set DIST_DIR=dist\SML-App-%CUSTID%
mkdir "%DIST_DIR%" 2>nul
copy dist\SML-App.exe "%DIST_DIR%\SML-App.exe"
copy sml-app.config "%DIST_DIR%\sml-app.config"

:: Include a README for the tester
(
echo SML-App - %CUSTID%
echo.
echo 1. Double-click SML-App.exe to launch
echo 2. Your browser will open automatically at http://localhost:5000
echo 3. First launch only: go to http://localhost:5000/setup.html and enter
echo    your Cognito password once - this stores it securely in your
echo    Windows credential manager so you won't be asked again
echo 4. A console window shows status - keep it open while using the app
echo 5. Close the console window to quit
) > "%DIST_DIR%\README.txt"

:: Zip using PowerShell (available on all modern Windows)
powershell -Command "Compress-Archive -Path '%DIST_DIR%\*' -DestinationPath 'dist\SML-App-%CUSTID%.zip' -Force"

echo.
echo -- Build complete ---------------------------------
echo Executable : dist\SML-App.exe
echo Tester ZIP : dist\SML-App-%CUSTID%.zip
echo.

endlocal
