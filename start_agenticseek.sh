#!/usr/bin/env bash
# Запуск AgenticSeek (backend + frontend) через Docker Compose
set -e

# Проверка наличия docker-compose
if ! command -v docker-compose &> /dev/null; then
  echo "[ERROR] Не найден docker-compose. Установите Docker Compose и повторите попытку."
  exit 1
fi

echo "[AgenticSeek] Запуск backend и frontend через docker-compose..."
docker-compose up --build