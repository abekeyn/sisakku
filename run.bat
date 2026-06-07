@echo off
cd /d "%~dp0"
echo Starting Kome system...
echo A browser will open automatically. To stop, press Ctrl+C in this window.
echo.
".venv\Scripts\python.exe" -m streamlit run Home.py
echo.
echo Server stopped.
pause
