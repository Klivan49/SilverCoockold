@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."

echo SilverCoockold: установка для Windows
echo.

where py >nul 2>nul
if errorlevel 1 (
    echo [Ошибка] Не найден лаунчер Python "py".
    echo Установите Python 3.12 с https://www.python.org/downloads/windows/
    echo При установке отметьте "Add python.exe to PATH" и компонент tcl/tk.
    pause
    exit /b 1
)

py -3.12 --version >nul 2>nul
if errorlevel 1 (
    echo [Ошибка] Python 3.12 не найден.
    echo Установите Python 3.12 с https://www.python.org/downloads/windows/
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Создаю виртуальное окружение .venv ...
    py -3.12 -m venv ".venv"
    if errorlevel 1 (
        echo [Ошибка] Не удалось создать .venv
        pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [Ошибка] Не удалось активировать .venv
    pause
    exit /b 1
)

echo Устанавливаю зависимости ...
python -m pip install --upgrade pip
if errorlevel 1 (
    echo [Ошибка] Не удалось обновить pip
    pause
    exit /b 1
)
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [Ошибка] Не удалось установить зависимости
    pause
    exit /b 1
)

python -c "import tkinter" >nul 2>nul
if errorlevel 1 (
    echo [Предупреждение] В Python нет tkinter.
    echo Для UI установите официальный Python 3.12 с python.org.
)

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo Создан .env из .env.example
)

echo.
echo Готово. Дальше:
echo   1. Откройте .env и заполните TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID
echo   2. Проверка: .venv\Scripts\activate.bat ^&^& python -m app --check
echo   3. Демон: windows\start_daemon.bat
echo   4. Интерфейс: windows\start_ui.bat
echo.
pause
endlocal
