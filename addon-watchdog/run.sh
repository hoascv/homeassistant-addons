#!/bin/bash
set -e
echo "[Add-on Watchdog] Starting..."
cd /app
exec python3 app.py
