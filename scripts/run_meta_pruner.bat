@echo off
REM 작업 스케줄러용 래퍼 — Meta weekly_pruner 실행
REM stdout/stderr를 logs/schtasks_meta.log 에 누적

setlocal
set "ROOT=C:\Users\damho\Desktop\royalhof-70won"
set "PY=C:\Users\damho\AppData\Local\Programs\Python\Python311\python.exe"
set "LOG=%ROOT%\logs\schtasks_meta.log"

cd /D "%ROOT%"
echo. >> "%LOG%"
echo ===== %DATE% %TIME% START ===== >> "%LOG%"
"%PY%" scripts\meta_weekly_pruner.py >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo ===== %DATE% %TIME% END (rc=%RC%) ===== >> "%LOG%"
exit /b %RC%
