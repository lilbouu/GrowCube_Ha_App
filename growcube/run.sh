#!/usr/bin/with-contenv sh
set -eu

export PYTHONUNBUFFERED=1

if [ -d /config ]; then
  mkdir -p /config/www/growcube
  cp /app/www/growcube-card.js /config/www/growcube/growcube-card.js
  echo "GrowCube Lovelace card copied to /config/www/growcube/growcube-card.js"
else
  echo "Home Assistant /config directory is not mounted; Lovelace card was not copied"
fi

exec python3 /app/main.py
