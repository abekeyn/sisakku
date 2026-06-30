@echo off
rem 鉄板焼きかいか（グラナダ）様 月次請求 自動ジョブ ランチャー
rem Windowsタスクスケジューラから毎月末日18:00に呼ばれる想定。
rem ログは logs\granada_YYYYMMDD.log に追記する。
setlocal
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
set "LOG=logs\granada_%date:~0,4%%date:~5,2%%date:~8,2%.log"
echo ==== %date% %time% START ==== >> "%LOG%"
".venv\Scripts\python.exe" -X utf8 -m lib.granada_monthly >> "%LOG%" 2>&1
echo ==== %date% %time% END (exit %errorlevel%) ==== >> "%LOG%"
endlocal
