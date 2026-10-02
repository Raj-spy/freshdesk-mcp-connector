#!/usr/bin/env bash
# One command: start the mock Freshdesk, run the demo, stop the mock.
set -e
cd "$(dirname "$0")/.."
export PYTHONPATH=.

uvicorn mock_freshdesk.server:app --port 9000 --log-level warning &
MOCK_PID=$!
trap 'kill $MOCK_PID 2>/dev/null' EXIT

for _ in $(seq 1 40); do
  curl -s localhost:9000/_mock/stats >/dev/null && break
  sleep 0.25
done

python scripts/demo.py 2>/dev/null
