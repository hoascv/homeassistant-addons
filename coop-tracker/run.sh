#!/bin/bash
set -e
echo "[Coop Tracker] Starting..."
cd /app
exec python3 app.py
