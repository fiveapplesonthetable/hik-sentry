# hik-sentry

A **standalone, phone-free daily report** for a Hik-Connect DVR. It reads the
cameras' *own* motion-detection events from Hik's cloud, runs **YOLO object
detection** to keep only the events that actually contain a person / vehicle /
animal, and emails you **one chronological cross-camera video** of the real
events each morning.

No live capture. No local 24/7 recording. No home/metered bandwidth. The camera
already does motion detection and uploads a small thumbnail per event to Hik's
cloud — we just read those, filter them with AI, and compile the highlights.

```
Hik cloud (/v3/alarms)  ──►  thumbnails (apiieu.ezvizlife.com)
        │                          │
        │                          ▼
   motion events            YOLOv8x: keep person/vehicle/animal, drop noise
   (free, on-device)               │
                                   ▼
                    one chronological cross-camera video + contact sheet  ──►  email
```

> **Real result (1 day):** 1,941 motion events → **91 real detections**
> (cat 48, person 42, bicycle 1). The other ~95% were shadows/IR/foliage —
> filtered out automatically.

---

## Why this design

A Hik DVR over-triggers motion *wildly* — ~14k events over a week, one every
couple of seconds from shadows, IR, insects, rain. Scrolling that is useless.
**Object detection is the filter** that turns thousands of noise events into
"a person at 16:04, a car at 16:51, the cat again at 22:40."

And it's friendly to a **metered home connection**: the camera uploads a tiny
JPEG thumbnail per motion event to Hik's cloud *anyway* (for the app's
notifications). We read those thumbnails from Hik's servers — **the live video
never leaves your house**, so it costs ~0 of your home data.

## How it works

1. **List motion events** — `GET https://apiieu.hik-connect.com/v3/alarms`
   with the account session. Paginate with the **`lastTime` cursor** (the last
   event's `"YYYY-MM-DD HH:MM:SS"` string — *not* offset, which the API ignores,
   and *not* epoch ms, which 500s).
2. **Download thumbnails** — each event's `picUrl` (≈100 KB JPEG on Hik's CDN),
   in parallel.
3. **YOLO filter** — `yolov8x` on every thumbnail, parallel across all cores,
   keep `person / bicycle / car / motorcycle / bus / truck / dog / cat / bird`.
4. **Compile + email** — one chronological cross-camera slideshow video + a
   labelled contact sheet, sent via SMTP.

**Retention:** Hik's free cloud keeps motion thumbnails for **~7 days**, so the
daily run must happen at least weekly (the timer handles it).

## Files

| File | Purpose |
|---|---|
| `cloud_report.py` | The pipeline: fetch day → thumbnails → parallel YOLO → video + email |
| `live_watch.py` | Optional real-time poller (process new events as they arrive) |
| `run-daily.sh` | Wrapper: process *yesterday* and email |
| `hik-sentry.timer` / `.service` | systemd-user units (06:30 daily) — see install |
| `SECURITY_REVIEW.md` | Authorized security review of the Hik-Connect app (bundled EOL OpenSSL, cert-validation gaps) |
| `motion.py`, `capture.py`, `daily_report.py`, `yolo_report.py` | Earlier "Tier-1" approach (local capture + own motion detection) — superseded by the cloud-thumbnail design above, kept for reference |

## Setup

```bash
python3 -m venv venv && . venv/bin/activate
pip install requests opencv-python-headless numpy ultralytics
#   plus ffmpeg on PATH, and a working Hik-Connect session via the companion
#   reverse-engineered client (see github.com/fiveapplesonthetable/hik-connect-py:
#   scripts/hik.py login --email … --password …)

# one-off, for any day in the last 7:
python3 cloud_report.py 2026-05-28 --conf 0.25

# install the daily timer (systemd-user):
cp hik-sentry.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hik-sentry.timer    # fires 06:30 daily on yesterday
loginctl enable-linger "$USER"                    # run even when logged out
```

## Config knobs

- **Detection classes** — edit `INTEREST` in `cloud_report.py` (default:
  person/vehicle/animal). Widen for deliveries (`suitcase, backpack`) or all
  COCO animals; narrow to `person` only; or make it per-camera.
- **Model** — `yolov8x` (max accuracy, ~15 min for a full day on a many-core
  box) vs `yolov8m` (~4× faster, slightly less recall on tiny/dark figures).
- **Confidence** — `--conf` (default 0.25).

## Recipients

Who gets the daily email is easy to set — first match wins:

```bash
# 1. one-off, on the command line:
python3 cloud_report.py 2026-05-28 --to me@x.com,family@x.com

# 2. environment variable (good for the timer/service):
RECIPIENTS="me@x.com family@x.com" python3 cloud_report.py 2026-05-28

# 3. a file (gitignored) — the usual choice for the daily run:
cp recipients.txt.example recipients.txt   # then edit, one address per line
```

If none of those are set, the report goes to the account owner only. Mail is
sent via the local `msmtp` (no third-party service).

## Relation to the other repos

- Cloud session + the cracked Hik streaming protocol: **hik-connect-py**.
- This repo is the analytics layer on top: motion events → AI → daily digest.

## Disclaimer

For personal use with your own Hik-Connect account and your own cameras.
Reverse engineering for interoperability with a service you legitimately use.
Not affiliated with Hikvision. Respect privacy — these are your cameras.
