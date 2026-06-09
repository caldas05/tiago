@echo off
REM Build a standalone polytime.exe.
REM Prereq (one-time):  .venv\Scripts\python.exe -m pip install -r requirements-dev.txt

set PY=.venv\Scripts\python.exe
if not exist %PY% set PY=python

%PY% -m PyInstaller --onefile --noconsole --name polytime ^
  --add-data "model;model" ^
  --add-data "transforms;transforms" ^
  --add-data "score_io;score_io" ^
  --additional-hooks-dir hooks ^
  --hidden-import music21 ^
  app.py
echo.
echo Done. See dist\polytime.exe
