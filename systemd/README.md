# systemd user units

Install:
    cp systemd/hik-*.{service,timer} ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now hik-sentry.timer hik-refresh.timer

- **hik-sentry.timer** → 06:30 daily: builds + emails yesterday's motion-event video.
- **hik-refresh.timer** → 00:00 & 12:00: keeps the Hik-Connect session token warm
  (tokens expire ~24h). Belt-and-suspenders — `cloud_report.py` also force-refreshes
  at the start of each run and self-heals on a mid-run 401.
