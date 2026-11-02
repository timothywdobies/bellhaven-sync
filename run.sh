#!/bin/sh
# One command: set up, refresh the review queue, and open the review app on port 5050.
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
.venv/bin/python pipeline.py
echo "Review app: http://127.0.0.1:5050"
exec .venv/bin/python app.py
