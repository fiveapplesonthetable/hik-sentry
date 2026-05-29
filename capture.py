#!/usr/bin/env python3
"""hik-sentry capture: record continuous ~60s MP4 segments per camera.

The Hik VTM drops idle sockets after ~75s, so we record in <=60s chunks and
loop. Each camera runs its own loop; cameras run in parallel. Segments land in
footage/camN/YYYYMMDD_HHMMSS.mp4 for later motion analysis.

Usage: capture.py <total_seconds> <ch> [ch ...]
"""
import os, subprocess, sys, time, threading, pathlib, datetime

HIK = pathlib.Path("/mnt/agent/hikvision_e2e")
PY = str(HIK / "venv" / "bin" / "python3")
HIKPY = str(HIK / "scripts" / "hik.py")
ROOT = pathlib.Path(__file__).resolve().parent
FOOT = ROOT / "footage"
CHUNK = 60  # seconds per segment


def record_channel(ch: int, end_ts: float):
    cam_dir = FOOT / f"cam{ch}"
    cam_dir.mkdir(parents=True, exist_ok=True)
    while time.time() < end_ts:
        dur = int(min(CHUNK, end_ts - time.time()))
        if dur < 3:
            break
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out = cam_dir / f"{ts}.mp4"
        try:
            subprocess.run(
                [PY, HIKPY, "ffmpeg", "--channel", str(ch),
                 "--duration", str(dur), "--no-audio", "--out", str(out)],
                cwd=str(HIK), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=dur + 30)
        except Exception as e:
            print(f"  cam{ch}: chunk error {e}", flush=True)
            time.sleep(2)
        sz = out.stat().st_size if out.exists() else 0
        print(f"  cam{ch}: {out.name} {sz//1024} KiB", flush=True)


def main():
    total = int(sys.argv[1])
    chans = [int(x) for x in sys.argv[2:]]
    end_ts = time.time() + total
    print(f"[*] capturing {chans} for {total}s -> {FOOT}", flush=True)
    threads = [threading.Thread(target=record_channel, args=(c, end_ts), daemon=True)
               for c in chans]
    for t in threads: t.start()
    for t in threads: t.join()
    print("[*] capture done", flush=True)


if __name__ == "__main__":
    main()
