@echo off
REM ── Запуск на Windows в один двойной клик ─────────────────────────────
REM Требуется установленный Python 3.10+ (python.org, при установке отметьте "Add to PATH")

cd /d "%~dp0"

if not exist .venv (
  echo [1/3] Создаю виртуальное окружение...
  python -m venv .venv
)

call .venv\Scripts\activate.bat

echo [2/3] Устанавливаю зависимости...
python -m pip install --upgrade pip >nul
pip install -r backend\requirements.txt

if not exist .env (
  copy .env.example .env >nul
  echo Создан файл .env — впишите туда ANTHROPIC_API_KEY для анализа фото.
)

echo [3/3] Запускаю сервер на http://127.0.0.1:8000
cd backend
uvicorn main:app --host 127.0.0.1 --port 8000

pause
