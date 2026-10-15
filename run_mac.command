#!/bin/bash
# Triody AI on macOS or Linux. Needs Python 3.11 or newer.
cd "$(dirname "$0")" || exit 1
PY=python3
command -v "$PY" >/dev/null 2>&1 || { echo "python3 not found. Install it from https://www.python.org"; read -r; exit 1; }
echo "Running tests..."
"$PY" -m unittest -q || { echo "Tests failed. See the messages above."; read -r; exit 1; }
echo
read -r -p "Use Triody on your phone too (same Wi-Fi)? Type y and press Enter, or just press Enter: " LAN
EXTRA=""
[ "$LAN" = "y" ] || [ "$LAN" = "Y" ] && EXTRA="--lan"
echo "Starting the dashboard at http://127.0.0.1:8000  (Ctrl+C to stop)"
( sleep 1; open http://127.0.0.1:8000 2>/dev/null || xdg-open http://127.0.0.1:8000 2>/dev/null ) &
"$PY" -m triody serve $EXTRA
