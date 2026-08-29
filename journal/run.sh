#!/bin/bash
set -e
echo "[Journal] Starting..."
cd /app
exec python3 app.py
