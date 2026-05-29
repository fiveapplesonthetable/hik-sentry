#!/usr/bin/env python3
"""hik-sentry Tier-2 report: re-score motion events with YOLO, keep/label only
those containing a person / vehicle / animal, annotate, and email.

Usage: yolo_report.py [YYYY-MM-DD] [--model yolov8x.pt] [--conf 0.3]
"""
import sys, json, pathlib, datetime, subprocess, collections
import cv2
from ultralytics import YOLO

ROOT = pathlib.Path(__file__).resolve().parent
EVENTS = ROOT / "events"
REPORTS = ROOT / "reports"
SEND = "/home/zim/.claude/skills/send-email/send_email.py"
INTEREST = {"person","bicycle","car","motorcycle","bus","truck","dog","cat","bird"}


def best_detection(model, clip, conf, every=5):
    cap = cv2.VideoCapture(str(clip))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 50
    best = None  # (label, conf, frame, box)
    for i in range(0, n, every):   # dense sampling — the subject may be brief
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, fr = cap.read()
        if not ok:
            continue
        for b in model(fr, verbose=False, conf=conf)[0].boxes:
            lab = model.names[int(b.cls)]; cf = float(b.conf)
            if lab in INTEREST and (best is None or cf > best[1]):
                best = (lab, cf, fr.copy(), [int(x) for x in b.xyxy[0]])
    cap.release()
    return best


def annotate(frame, box, label, conf, cam, t):
    x1, y1, x2, y2 = box
    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 220, 0), 3)
    cap = f"{label} {conf:.2f}"
    cv2.rectangle(frame, (x1, y1-24), (x1+len(cap)*12, y1), (0,220,0), -1)
    cv2.putText(frame, cap, (x1+3, y1-6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,0,0), 2, cv2.LINE_AA)
    cv2.rectangle(frame, (0,0), (frame.shape[1],26), (0,0,0), -1)
    cv2.putText(frame, f"{cam}  {t}", (6,18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,255),1,cv2.LINE_AA)
    return frame


def main():
    day = next((a for a in sys.argv[1:] if not a.startswith("--")),
               datetime.date.today().isoformat())
    model_name = "yolov8x.pt"; conf = 0.30
    if "--model" in sys.argv: model_name = sys.argv[sys.argv.index("--model")+1]
    if "--conf" in sys.argv: conf = float(sys.argv[sys.argv.index("--conf")+1])
    rows = [json.loads(l) for l in (EVENTS/day/"events.jsonl").read_text().splitlines() if l.strip()]
    rows.sort(key=lambda r: r["time"])
    model = YOLO(model_name)
    print(f"[*] re-scoring {len(rows)} motion events with {model_name} (conf>={conf})")
    REPORTS.mkdir(exist_ok=True)
    ydir = REPORTS / f"{day}_yolo"; ydir.mkdir(exist_ok=True)
    kept = []
    for r in rows:
        clip = EVENTS / r["clip"]
        det = best_detection(model, clip, conf)
        t = r["time"].split("T")[1]
        if det:
            lab, cf, frame, box = det
            ann = ydir / f"{r['cam']}_{t.replace(':','')}_{lab}.jpg"
            cv2.imwrite(str(ann), annotate(frame, box, lab, cf, r["cam"], t))
            r2 = dict(r, label=lab, conf=round(cf,2), annotated=str(ann))
            kept.append(r2)
            print(f"  KEEP {r['cam']} {t}: {lab} {cf:.2f}")
        else:
            print(f"  drop {r['cam']} {t}: no person/vehicle/animal")
    # contact sheet of kept (annotated) frames
    sheet = REPORTS / f"{day}_yolo_sheet.jpg"
    _grid([k["annotated"] for k in kept], sheet)
    # reel of kept clips, labelled
    reel = REPORTS / f"{day}_yolo_highlights.mp4"
    _reel(kept, reel)
    # summary
    by = collections.Counter(k["label"] for k in kept)
    summary = (f"hik-sentry YOLO report — {day}\n\n"
               f"{len(kept)} of {len(rows)} motion events contained a "
               f"person/vehicle/animal ({model_name}).\n\n"
               + "\n".join(f"  {lab}: {n}" for lab,n in by.most_common()) + "\n\nTimeline:\n"
               + "\n".join(f"  {k['time'].split('T')[1]}  {k['cam']}  {k['label']} ({k['conf']})" for k in kept))
    body = REPORTS / f"{day}_yolo_summary.txt"; body.write_text(summary)
    print("\n"+summary)
    attach = []
    if sheet.exists(): attach += ["--attach", str(sheet)]
    if reel.exists(): attach += ["--attach", str(reel)]
    subprocess.run(["python3", SEND, "--subject",
                    f"[hik-sentry] {day} YOLO — {len(kept)} confirmed (person/vehicle/animal)",
                    "--body-file", str(body), *attach])


def _grid(imgs, out):
    import numpy as np
    cells=[]
    for p in imgs:
        im=cv2.imread(p)
        if im is None: continue
        w=360; cells.append(cv2.resize(im,(w,int(im.shape[0]*w/im.shape[1]))))
    if not cells: return
    cols=min(3,len(cells)); ch=max(c.shape[0] for c in cells); w=360
    def pad(c):
        p=np.zeros((ch,w,3),np.uint8); p[:c.shape[0]]=c; return p
    cells=[pad(c) for c in cells]; blank=np.zeros((ch,w,3),np.uint8)
    rws=[]
    for i in range(0,len(cells),cols):
        row=cells[i:i+cols]
        while len(row)<cols: row.append(blank)
        rws.append(np.hstack(row))
    cv2.imwrite(str(out), np.vstack(rws))


def _reel(kept, out):
    import tempfile
    if not kept: return
    tmp=pathlib.Path(tempfile.mkdtemp()); parts=[]
    for i,k in enumerate(kept):
        clip=EVENTS/k["clip"];
        if not clip.exists(): continue
        lab=f"{k['cam']} {k['time'].split('T')[1].replace(':',chr(92)+':')}  {k['label']} {k['conf']}"
        p=tmp/f"p{i:03d}.mp4"
        subprocess.run(["ffmpeg","-y","-i",str(clip),"-vf",
            f"scale=960:-2,drawtext=text='{lab}':x=10:y=10:fontsize=26:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=6",
            "-r","25","-an","-c:v","libx264","-preset","veryfast","-pix_fmt","yuv420p",str(p)],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if p.exists() and p.stat().st_size>0: parts.append(p)
    if not parts: return
    lst=tmp/"l.txt"; lst.write_text("".join(f"file '{p}'\n" for p in parts))
    subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(lst),"-c","copy",str(out)],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
