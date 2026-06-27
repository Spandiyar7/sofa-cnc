#!/usr/bin/env bash
# Запуск на macOS/Linux одной командой: bash run.sh
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "[1/3] Создаю виртуальное окружение..."
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "[2/3] Устанавливаю зависимости..."
python -m pip install --upgrade pip >/dev/null
pip install -r backend/requirements.txt

[ -f .env ] || cp .env.example .env

echo "[3/3] Запуск на http://127.0.0.1:8000"
cd backend
uvicorn main:app --host 127.0.0.1 --port 8000
