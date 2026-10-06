@echo off
cd /d "%~dp0"
python -m nhl_tim.cli build_site
start "" "%~dp0board.html"
