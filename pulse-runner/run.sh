#!/bin/bash
set -e
echo "[Pulse Runner] Starting..."
cd /app
exec python3 app.py
