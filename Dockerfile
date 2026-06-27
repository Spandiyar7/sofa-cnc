# Контейнер для запуска платформы на любом облачном хостинге (Render/Railway/Fly).
FROM python:3.12-slim

WORKDIR /app

# Системные библиотеки для ezdxf/matplotlib не нужны — ставим только Python-зависимости
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY . .

# Папки для БД и логов (на бесплатных тарифах диск эфемерный — история сбросится при передеплое)
RUN mkdir -p data logs

WORKDIR /app/backend

# Хостинг передаёт порт через переменную $PORT
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
