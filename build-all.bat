@echo off
:: build-all.bat — Build personalised SML-App packages for a list of users
:: Usage: build-all.bat <users.csv>
:: CSV format (with header row): custid,email
::
:: Example users.csv:
::   custid,email
::   tGwuZQqEcx,mtempler@gmail.com
::   aB3kR9mNpQ,user2@example.com

setlocal enabledelayedexpansion

if "%~1"=="" (
    echo Usage: build-all.bat ^<users.csv^>
    exit /b 1
)

set CSV=%~1
if not exist "%CSV%" (
    echo Error: file not found: %CSV%
    exit /b 1
)

set SUCCESS=0
set FAILED=0
set SKIPPED=0

echo.
echo ── SML-App Batch Build ────────────────────────────
echo Input: %CSV%
echo.

:: Skip header row, iterate remaining lines
set FIRST=1
for /f "usebackq tokens=1,2 delims=," %%A in ("%CSV%") do (
    if !FIRST!==1 (
        set FIRST=0
    ) else (
        set CUSTID=%%A
        set EMAIL=%%B

        :: Strip any trailing whitespace/CR from values
        set CUSTID=!CUSTID: =!
        set EMAIL=!EMAIL: =!

        if "!CUSTID!"=="" (
            echo [SKIP] Empty custid on line — skipping
            set /a SKIPPED+=1
        ) else (
            echo [BUILD] !CUSTID! / !EMAIL!
            call build.bat !CUSTID! !EMAIL!
            if errorlevel 1 (
                echo [FAIL]  !CUSTID!
                set /a FAILED+=1
            ) else (
                echo [OK]    !CUSTID! — dist\SML-App-!CUSTID!.zip
                set /a SUCCESS+=1
            )
            echo.
        )
    )
)

echo ── Summary ────────────────────────────────────────
echo Built:   %SUCCESS%
echo Failed:  %FAILED%
echo Skipped: %SKIPPED%
echo.
echo All ZIPs are in the dist\ folder.

endlocal
