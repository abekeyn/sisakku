@echo off
cd /d "%~dp0"
echo Starting Kome system...
echo A browser will open shortly. URL: http://localhost:8501
echo To stop, press Ctrl+C in this window.
echo If the browser shows "connection refused", wait a few seconds and reload.
echo.
start "" /b cmd /c "timeout /t 9 >nul & start "" http://localhost:8501"
".venv\Scripts\python.exe" -m streamlit run Home.py
echo.
echo Server stopped.
pause
