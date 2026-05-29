#!/usr/bin/env bash
# hik-sentry daily report — processes YESTERDAY's complete motion-event set
# (every event, no skipping) and emails the chronological detected-events video.
set -euo pipefail
cd "$(dirname "$0")"
DAY=$(date -u -d 'yesterday' +%Y-%m-%d)
# Recipients: edit recipients.txt (one address per line), or set RECIPIENTS=...,
# or append --to a@x.com,b@y.com below. Defaults to the account owner.
exec ./venv/bin/python3 cloud_report.py "$DAY" --conf 0.25 >> logs/daily.log 2>&1
