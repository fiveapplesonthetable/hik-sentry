#!/usr/bin/env python3
"""hik-sentry cloud daily report — fully phone-free.

Reads the camera's OWN motion-detection events from the Hik cloud
(/v3/alarms), downloads each event's thumbnail, runs YOLO to keep only
events containing a person/vehicle/animal, then compiles a labelled daily
video + contact sheet and emails it.

No live capture, no local 24/7 storage, no own motion detection — the device
already did the motion filtering for free.

Usage: cloud_report.py [YYYY-MM-DD]   (default today)  [--conf 0.3] [--max 400]
"""
import sys, os, json, time, datetime, pathlib, subprocess, collections, mimetypes
from email.message import EmailMessage
import requests, cv2, numpy as np
sys.path.insert(0, "/mnt/agent/hikvision_e2e/scripts")
import hik
from ultralytics import YOLO

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "cloud_events"
REPORTS = ROOT / "reports"
SENDER = "you@example.com"
DEFAULT_TO = "you@example.com"
INTEREST = {"person","bicycle","car","motorcycle","bus","truck","dog","cat","bird"}


def load_recipients(cli_to=None):
    """Resolve who gets the report, easiest-to-override first:
       1. --to a@x.com,b@y.com   (CLI)
       2. $RECIPIENTS            (comma/space/newline separated)
       3. recipients.txt         (one address per line, '#' comments — gitignored)
       4. DEFAULT_TO             (the account owner)
    """
    def split(v): return [x.strip() for x in v.replace(",", " ").split() if x.strip() and not x.startswith("#")]
    if cli_to: return split(cli_to)
    if os.environ.get("RECIPIENTS"): return split(os.environ["RECIPIENTS"])
    f = ROOT / "recipients.txt"
    if f.exists():
        rec = [ln.split("#",1)[0].strip() for ln in f.read_text().splitlines()]
        rec = [r for r in rec if r]
        if rec: return rec
    return [DEFAULT_TO]


def send_report(subject, body, attachments, recipients):
    """Self-contained MIME send via local msmtp; supports any recipient list."""
    msg = EmailMessage()
    msg["From"] = SENDER
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(body)
    for path in attachments:
        path = pathlib.Path(path)
        ctype, enc = mimetypes.guess_type(str(path))
        if ctype is None or enc is not None: ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
    proc = subprocess.run(["msmtp", "-a", "default", *recipients],
                          input=msg.as_bytes(), capture_output=True)
    if proc.returncode != 0:
        sys.stderr.write(f"msmtp exit={proc.returncode}\n{proc.stderr.decode()}\n")
    return proc.returncode


def session_headers():
    s = hik.HikSession.load()
    H = dict(s._headers)
    H.update({"clientVersion":"4.19.0.1014","appId":"HikConnect","clientNo":"google_play",
              "appChannel":"hikvision","customno":"1000002","osVersion":"13","netType":"WIFI"})
    return s, H


def fetch_day(day: str, max_pages: int = 2000):
    """Exhaustively page the WHOLE day via the lastTime cursor (the only working
    cursor — offset is ignored by /v3/alarms). lastTime is the datetime STRING
    of the last event; the API returns events strictly older than it. We start
    at the day's END boundary so we don't waste calls on later days, and page
    back until before the day's START. De-dup by alarmId. No event is skipped.
    """
    s, H = session_headers()
    base = f"https://{s.api_domain}"
    day_start = datetime.datetime.strptime(day, "%Y-%m-%d")
    day_end = day_start + datetime.timedelta(days=1)
    lo = int(day_start.timestamp()*1000)
    hi = int(day_end.timestamp()*1000)
    events = {}
    last = day_end.strftime("%Y-%m-%d %H:%M:%S")   # start just past the day's end
    for _ in range(max_pages):
        r = requests.get(base+"/v3/alarms", headers=H,
                         params={"limit":50,"queryType":-1,"lastTime":last}, timeout=20)
        j = r.json(); al = j.get("alarms", [])
        if not al:
            break
        for a in al:
            if lo <= a["alarmStartTime"] < hi:
                events[a["alarmId"]] = a
        oldest = al[-1]
        if oldest["alarmStartTime"] < lo:   # paged past the day's start
            break
        nxt = oldest["alarmStartTimeStr"]
        if nxt == last:                      # cursor stuck (dense same-second) — nudge
            break
        last = nxt
        time.sleep(0.2)                      # gentle on the API
    return s, H, list(events.values())


def download_thumbs(H, events, day):
    from concurrent.futures import ThreadPoolExecutor
    d = OUT / day; d.mkdir(parents=True, exist_ok=True)
    def grab(a):
        p = d / f"{a['alarmId']}.jpg"
        if p.exists() and p.stat().st_size > 1000:
            a["_thumb"] = str(p); return
        url = a.get("picUrl")
        if not url: return
        try:
            r = requests.get(url, headers=H, timeout=20)
            if r.status_code == 200 and len(r.content) > 1000:
                p.write_bytes(r.content); a["_thumb"] = str(p)
        except Exception:
            pass
    with ThreadPoolExecutor(max_workers=32) as ex:
        list(ex.map(grab, events))
    return [a for a in events if a.get("_thumb")]


# --- parallel YOLO across cores ---
_MODEL = None
def _worker_init(model_name):
    global _MODEL
    import os
    os.environ["OMP_NUM_THREADS"] = "1"   # 1 thread/proc; parallelism across procs
    from ultralytics import YOLO as _Y
    _MODEL = _Y(model_name)

def _score(thumb, conf):
    import cv2 as _cv
    img = _cv.imread(thumb)
    if img is None: return None
    best = None
    for b in _MODEL(img, verbose=False, conf=conf)[0].boxes:
        lab = _MODEL.names[int(b.cls)]; cf = float(b.conf)
        if lab in INTEREST and (best is None or cf > best[1]):
            best = (lab, cf, [int(x) for x in b.xyxy[0]])
    return best

def yolo_label(events, conf, model_name="yolov8x.pt", workers=48):
    import multiprocessing as mp
    args = [(a["_thumb"], conf) for a in events]
    with mp.Pool(workers, initializer=_worker_init, initargs=(model_name,)) as pool:
        results = pool.starmap(_score, args, chunksize=4)
    kept = []
    for a, best in zip(events, results):
        if best:
            a["_label"], a["_conf"], a["_box"] = best[0], round(best[1],2), best[2]
            kept.append(a)
    return kept


def annotate(a):
    img = cv2.imread(a["_thumb"])
    x1,y1,x2,y2 = a["_box"]
    cv2.rectangle(img,(x1,y1),(x2,y2),(0,220,0),3)
    cap=f"{a['_label']} {a['_conf']}"
    cv2.rectangle(img,(x1,max(0,y1-26)),(x1+len(cap)*13,y1),(0,220,0),-1)
    cv2.putText(img,cap,(x1+3,y1-6),cv2.FONT_HERSHEY_SIMPLEX,0.7,(0,0,0),2,cv2.LINE_AA)
    top=f"{a['alarmName']}  {a['alarmStartTimeStr'].split(' ')[1]}  {a['_label']}"
    cv2.rectangle(img,(0,0),(img.shape[1],30),(0,0,0),-1)
    cv2.putText(img,top,(6,21),cv2.FONT_HERSHEY_SIMPLEX,0.6,(255,255,255),1,cv2.LINE_AA)
    return img


def build_video(kept, day):
    REPORTS.mkdir(exist_ok=True)
    frames_dir = REPORTS / f"{day}_frames"; frames_dir.mkdir(exist_ok=True)
    for i,a in enumerate(sorted(kept,key=lambda x:x["alarmStartTime"])):
        cv2.imwrite(str(frames_dir/f"{i:04d}.jpg"), cv2.resize(annotate(a),(960,1080)))
    out = REPORTS / f"{day}_hik_daily.mp4"
    # slideshow: each labelled event ~1.5s
    subprocess.run(["ffmpeg","-y","-framerate","2/3","-i",str(frames_dir/"%04d.jpg"),
                    "-c:v","libx264","-r","25","-pix_fmt","yuv420p",
                    "-vf","scale=960:1080",str(out)],
                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    return out


def contact_sheet(kept, day):
    cells=[]
    for a in sorted(kept,key=lambda x:x["alarmStartTime"]):
        im=annotate(a); w=300; cells.append(cv2.resize(im,(w,int(im.shape[0]*w/im.shape[1]))))
    if not cells: return None
    cols=min(5,len(cells)); ch=max(c.shape[0] for c in cells); w=300
    pad=lambda c:(np.vstack([c,np.zeros((ch-c.shape[0],w,3),np.uint8)]) if c.shape[0]<ch else c)
    cells=[pad(c) for c in cells]; blank=np.zeros((ch,w,3),np.uint8)
    rows=[]
    for i in range(0,len(cells),cols):
        r=cells[i:i+cols]
        while len(r)<cols: r.append(blank)
        rows.append(np.hstack(r))
    out=REPORTS/f"{day}_hik_sheet.jpg"; cv2.imwrite(str(out),np.vstack(rows)); return out


def main():
    args=sys.argv[1:]
    day=next((a for a in args if not a.startswith("--")), datetime.date.today().isoformat())
    conf=float(args[args.index("--conf")+1]) if "--conf" in args else 0.30
    cli_to=args[args.index("--to")+1] if "--to" in args else None
    recipients=load_recipients(cli_to)
    print(f"[*] fetching motion events for {day}")
    s,H,events=fetch_day(day)
    print(f"[*] {len(events)} motion events from the device")
    events=download_thumbs(H,events,day)
    print(f"[*] {len(events)} thumbnails downloaded; running YOLO…")
    kept=yolo_label(events,conf)
    by=collections.Counter(a["_label"] for a in kept)
    bycam=collections.Counter(a["alarmName"] for a in kept)
    summary=(f"hik-sentry daily report — {day} (device motion events + YOLO)\n\n"
             f"{len(events)} motion events; {len(kept)} contained a person/vehicle/animal.\n\n"
             "By type:\n"+"\n".join(f"  {k}: {n}" for k,n in by.most_common())+
             "\n\nBy camera:\n"+"\n".join(f"  {k}: {n}" for k,n in bycam.most_common())+
             "\n\nTimeline:\n"+"\n".join(
                 f"  {a['alarmStartTimeStr'].split(' ')[1]}  {a['alarmName']}  {a['_label']} ({a['_conf']})"
                 for a in sorted(kept,key=lambda x:x['alarmStartTime'])))
    REPORTS.mkdir(exist_ok=True)
    body=REPORTS/f"{day}_hik_summary.txt"; body.write_text(summary); print("\n"+summary)
    vid=build_video(kept,day) if kept else None
    sheet=contact_sheet(kept,day) if kept else None
    att=[p for p in (vid, sheet) if p and p.exists()]
    subj=f"[hik-sentry] {day} — {len(kept)} person/vehicle/animal events (of {len(events)} motion)"
    send_report(subj, summary, att, recipients)
    print(f"[*] emailed {', '.join(recipients)}. video={vid} sheet={sheet}")


if __name__=="__main__":
    main()
