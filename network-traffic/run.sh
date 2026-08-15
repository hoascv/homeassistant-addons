#!/bin/bash
set -e
echo "[Network Traffic Monitor] Starting..."
cd /app
exec python3 app.py
