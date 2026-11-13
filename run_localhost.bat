@echo off
title AI Agent Binh Minh - Localhost
cd /d "%~dp0"
echo ========================================================
echo   KHOI DONG AI AGENT KINH DOANH TUAN - CONG TY BINH MINH
echo   May chu Localhost: http://localhost:8080
echo ========================================================
echo.
.venv\Scripts\python.exe server.py --host 127.0.0.1 --port 8080
pause

