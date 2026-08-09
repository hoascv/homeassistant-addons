#!/bin/bash
set -e
echo "[Detection Hub] Starting..."
cd /app
exec python3 app.py
