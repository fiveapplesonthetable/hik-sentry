#!/usr/bin/env python3
"""hik-sentry motion analysis (Tier 1).

Scans recorded MP4 segments, finds intervals where something actually moves
(background-subtraction + contour area), and emits "events": a thumbnail at
the peak motion frame + a short clip + a metadata line. Most footage is static,
so this throws away the boring 99% and keeps the interesting moments — the
"event rung" of the compression ladder.

Usage:
  motion.py scan <segment.mp4> [--cam N]          # analyze one file -> events
  motion.py scandir <footage_dir>                 # analyze all cams' segments

Tunables below (MIN_AREA, THRESH, etc.) — tuned against the captured footage.
"""
import sys, os, json, pathlib, datetime, subprocess
import cv2
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent
EVENTS = ROOT / "events"

# --- tunables ---
SAMPLE_FPS = 5            # analyze 5 frames/sec (cheap; motion doesn't need 25)
MIN_AREA_FRAC = 0.004     # changed-area must exceed 0.4% of frame to count
THRESH = 25               # pixel-diff threshold (0-255)
WARMUP = 5                # frames to learn the background before detecting
GAP_MERGE_S = 3.0         # merge motion bursts closer than this into one event
MIN_EVENT_S = 0.6         # ignore blips shorter than this


def analyze(path: pathlib.Path):
    """Return list of (start_s, end_s, peak_s, peak_score) motion intervals."""
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    step = max(1, int(round(fps / SAMPLE_FPS)))
    bg = cv2.createBackgroundSubtractorMOG2(history=200, varThreshold=24,
                                            detectShadows=True)
    idx = 0; seen = 0
    motion = []   # (t_sec, score_frac)
    frame_area = None
    while True:
        ok = cap.grab()
        if not ok:
            break
        if idx % step != 0:
            idx += 1; continue
        ok, frame = cap.retrieve()
        idx += 1
        if not ok:
            break
        t = idx / fps
        small = cv2.resize(frame, (480, 270))
        if frame_area is None:
            frame_area = small.shape[0] * small.shape[1]
        mask = bg.apply(small)
        seen += 1
        if seen < WARMUP:
            continue
        # shadow pixels are 127 in MOG2; keep only hard foreground
        _, mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)
        mask = cv2.medianBlur(mask, 5)
        changed = int(np.count_nonzero(mask))
        frac = changed / frame_area
        if frac >= MIN_AREA_FRAC:
            motion.append((t, frac))
    cap.release()
    return _intervals(motion)


def _intervals(motion):
    if not motion:
        return []
    events = []
    s = motion[0][0]; last = motion[0][0]; peak_t = motion[0][0]; peak = motion[0][1]
    for t, f in motion[1:]:
        if t - last > GAP_MERGE_S:
            if last - s >= MIN_EVENT_S:
                events.append((s, last, peak_t, peak))
            s = t; peak = f; peak_t = t
        if f > peak:
            peak = f; peak_t = t
        last = t
    if last - s >= MIN_EVENT_S:
        events.append((s, last, peak_t, peak))
    return events


def _seg_start_time(path: pathlib.Path):
    """Parse the wall-clock start time from the segment filename YYYYMMDD_HHMMSS."""
    try:
        return datetime.datetime.strptime(path.stem, "%Y%m%d_%H%M%S")
    except ValueError:
        return datetime.datetime.fromtimestamp(path.stat().st_mtime)


def emit_events(path: pathlib.Path, cam: str, events):
    if not events:
        return []
    day = _seg_start_time(path).strftime("%Y-%m-%d")
    outdir = EVENTS / day / cam
    outdir.mkdir(parents=True, exist_ok=True)
    seg_start = _seg_start_time(path)
    rows = []
    for (s, e, pt, score) in events:
        wall = seg_start + datetime.timedelta(seconds=pt)
        tag = wall.strftime("%H%M%S")
        thumb = outdir / f"{tag}.jpg"
        clip = outdir / f"{tag}.mp4"
        # thumbnail at peak motion
        subprocess.run(["ffmpeg", "-y", "-ss", f"{pt:.2f}", "-i", str(path),
                        "-frames:v", "1", "-q:v", "3", str(thumb)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # clip a few seconds around the event
        cs = max(0, s - 1.0)
        subprocess.run(["ffmpeg", "-y", "-ss", f"{cs:.2f}", "-i", str(path),
                        "-t", f"{(e - s) + 3:.2f}", "-c", "copy", str(clip)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        row = {"cam": cam, "time": wall.isoformat(timespec="seconds"),
               "score": round(score, 4), "dur_s": round(e - s, 1),
               "thumb": str(thumb.relative_to(EVENTS)),
               "clip": str(clip.relative_to(EVENTS)), "segment": path.name}
        rows.append(row)
        with open(EVENTS / day / "events.jsonl", "a") as f:
            f.write(json.dumps(row) + "\n")
    return rows


def scandir(footage: pathlib.Path):
    total = 0
    for cam_dir in sorted(footage.glob("cam*")):
        cam = cam_dir.name
        for seg in sorted(cam_dir.glob("*.mp4")):
            ev = analyze(seg)
            rows = emit_events(seg, cam, ev)
            if rows:
                print(f"  {cam}/{seg.name}: {len(rows)} event(s) "
                      f"(peak score {max(r['score'] for r in rows):.3f})")
            total += len(rows)
    print(f"[*] {total} events total -> {EVENTS}")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "scan":
        p = pathlib.Path(sys.argv[2])
        cam = "cam?"
        if "--cam" in sys.argv:
            cam = "cam" + sys.argv[sys.argv.index("--cam") + 1]
        ev = analyze(p)
        print(f"{len(ev)} motion intervals:", [(round(s,1), round(e,1), round(sc,3)) for s,e,_,sc in ev])
        emit_events(p, cam, ev)
    elif len(sys.argv) >= 3 and sys.argv[1] == "scandir":
        scandir(pathlib.Path(sys.argv[2]))
    else:
        print(__doc__)
