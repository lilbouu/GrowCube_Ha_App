#!/usr/bin/with-contenv sh
set -eu

export PYTHONUNBUFFERED=1

if [ -d /config ]; then
  mkdir -p /config/www/growcube
  cp /app/www/growcube-card.js /config/www/growcube/growcube-card.js
fi

exec python3 /app/main.py
