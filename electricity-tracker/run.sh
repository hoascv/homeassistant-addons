#!/bin/bash
set -e
echo "[Electricity Tracker] Starting..."
cd /app
exec python3 app.py
