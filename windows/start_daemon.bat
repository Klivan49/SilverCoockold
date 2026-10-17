@echo off
chcp 65001 >nul
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo Сначала запустите windows\install.bat
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"
python -m app --daemon
echo.
echo Демон остановлен.
pause
