#!/usr/bin/env python3
"""hik-sentry daily report (Tier 1).

Takes a day's motion events and produces:
  - a highlight reel: all event clips concatenated, each labelled cam + time
  - a contact sheet: a grid of event thumbnails
  - a text summary: counts per camera + a timeline
…then emails them.

Usage: daily_report.py [YYYY-MM-DD]   (default: today)
"""
import sys, os, json, pathlib, subprocess, datetime, tempfile, collections

ROOT = pathlib.Path(__file__).resolve().parent
EVENTS = ROOT / "events"
REPORTS = ROOT / "reports"
SEND = "/home/zim/.claude/skills/send-email/send_email.py"


def load_events(day: str):
    f = EVENTS / day / "events.jsonl"
    if not f.exists():
        return []
    rows = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    rows.sort(key=lambda r: r["time"])
    return rows


def make_reel(day, rows, out):
    """Concat event clips, each with a cam+time label burned in."""
    if not rows:
        return False
    tmp = pathlib.Path(tempfile.mkdtemp())
    parts = []
    for i, r in enumerate(rows):
        clip = EVENTS / r["clip"]
        if not clip.exists():
            continue
        t = r["time"].split("T")[1]
        # escape colons (drawtext option separator) and other specials
        label = f"{r['cam']}  {t}".replace("\\", "\\\\").replace(":", "\\:")
        lab = tmp / f"p{i:03d}.mp4"
        # re-encode each clip with a label so concat is clean + uniform
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(clip),
             "-vf", f"scale=960:-2,drawtext=text='{label}':x=10:y=10:fontsize=28:"
                    f"fontcolor=white:box=1:boxcolor=black@0.5:boxborderw=6",
             "-r", "25", "-an", "-c:v", "libx264", "-preset", "veryfast",
             "-pix_fmt", "yuv420p", str(lab)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if lab.exists() and lab.stat().st_size > 0:
            parts.append(lab)
    if not parts:
        return False
    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c", "copy", str(out)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out.exists()


def make_contact_sheet(rows, out):
    """Grid of event thumbnails (with cam+time captions) via OpenCV/numpy."""
    import cv2, numpy as np
    items = [(r, EVENTS / r["thumb"]) for r in rows if (EVENTS / r["thumb"]).exists()]
    if not items:
        return False
    W = 320
    cells = []
    for r, tp in items:
        img = cv2.imread(str(tp))
        if img is None:
            continue
        h = int(img.shape[0] * W / img.shape[1])
        img = cv2.resize(img, (W, h))
        cap = f"{r['cam']} {r['time'].split('T')[1]}"
        cv2.rectangle(img, (0, 0), (W, 22), (0, 0, 0), -1)
        cv2.putText(img, cap, (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (255, 255, 255), 1, cv2.LINE_AA)
        cells.append(img)
    if not cells:
        return False
    cols = min(4, len(cells))
    rows_n = (len(cells) + cols - 1) // cols
    ch = max(c.shape[0] for c in cells)
    # pad each cell to uniform height, then build the grid
    def pad(c):
        if c.shape[0] == ch:
            return c
        p = np.zeros((ch, W, 3), np.uint8); p[:c.shape[0]] = c; return p
    cells = [pad(c) for c in cells]
    blank = np.zeros((ch, W, 3), np.uint8)
    grid_rows = []
    for ri in range(rows_n):
        row_cells = cells[ri*cols:(ri+1)*cols]
        while len(row_cells) < cols:
            row_cells.append(blank)
        grid_rows.append(np.hstack(row_cells))
    sheet = np.vstack(grid_rows)
    cv2.imwrite(str(out), sheet)
    return out.exists()


def summarize(day, rows):
    by_cam = collections.Counter(r["cam"] for r in rows)
    lines = [f"hik-sentry daily report — {day}", "",
             f"{len(rows)} motion events across {len(by_cam)} cameras.", ""]
    for cam, n in sorted(by_cam.items()):
        lines.append(f"  {cam}: {n} events")
    lines += ["", "Timeline:"]
    for r in rows:
        t = r["time"].split("T")[1]
        lines.append(f"  {t}  {r['cam']}  ({r['dur_s']}s, score {r['score']})")
    lines += ["", "Highlight reel + contact sheet attached.",
              "(Tier 1: raw motion. Tier 2 will label person/vehicle/animal.)"]
    return "\n".join(lines)


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().isoformat()
    rows = load_events(day)
    REPORTS.mkdir(exist_ok=True)
    reel = REPORTS / f"{day}_highlights.mp4"
    sheet = REPORTS / f"{day}_contactsheet.jpg"
    body = REPORTS / f"{day}_summary.txt"
    summary = summarize(day, rows)
    body.write_text(summary)
    print(summary)
    if not rows:
        print("[*] no events; emailing summary only")
        subprocess.run(["python3", SEND, "--subject", f"[hik-sentry] {day} — no events",
                        "--body-file", str(body)])
        return
    has_reel = make_reel(day, rows, reel)
    has_sheet = make_contact_sheet(rows, sheet)
    attach = []
    if has_reel: attach += ["--attach", str(reel)]
    if has_sheet: attach += ["--attach", str(sheet)]
    print(f"[*] reel={has_reel} sheet={has_sheet} — emailing")
    subprocess.run(["python3", SEND, "--subject",
                    f"[hik-sentry] {day} — {len(rows)} events",
                    "--body-file", str(body), *attach])


if __name__ == "__main__":
    main()
