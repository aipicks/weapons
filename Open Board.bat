@echo off
cd /d "%~dp0"
echo Updating odds, stats and goalies...
python -m nhl_tim.cli update_daily
if errorlevel 1 goto fail
python -m nhl_tim.cli build_site
if errorlevel 1 goto fail
start "" "%~dp0board.html"
exit /b 0
:fail
echo.
echo Something failed - see the message above.
pause
