@echo off
cd /d "%~dp0"
py -3.11 -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo Setup complete. Run launch_windows.bat.
pause
exit /b 0
:failed
echo Setup failed. See the error above. Python 3.11 and internet access are required.
pause
exit /b 1
