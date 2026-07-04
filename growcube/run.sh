#!/usr/bin/with-contenv sh
set -eu

export PYTHONUNBUFFERED=1

exec python3 /app/main.py
