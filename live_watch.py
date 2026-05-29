#!/usr/bin/env python3
"""hik-sentry live watcher — phone-free, API-gentle.

Polls the camera's own motion-event feed (/v3/alarms, first page only) every
POLL seconds. For each NEW event: download its thumbnail, run YOLO, and if it
contains a person/vehicle/animal, save an annotated frame + a record line to
the day's store. The daily video is compiled later from those hits.

~1 API call/poll + a few thumbnail downloads per new event = light, no bulk
back-fetch, no rate-limit blast.

Usage: live_watch.py [--poll 45] [--conf 0.3] [--model yolov8x.pt]
"""
import sys, os, json, time, datetime, pathlib, requests, cv2
sys.path.insert(0, "/mnt/agent/hikvision_e2e/scripts"); import hik
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from cloud_report import session_headers, annotate, INTEREST
from ultralytics import YOLO

ROOT = pathlib.Path(__file__).resolve().parent
HITS = ROOT / "hits"          # hits/<day>/<alarmId>.jpg + events.jsonl
STATE = ROOT / "hits" / ".last_seen"


def day_dir(ts_ms):
    d = datetime.datetime.fromtimestamp(ts_ms/1000).strftime("%Y-%m-%d")
    p = HITS / d; p.mkdir(parents=True, exist_ok=True); return p, d


def main():
    args = sys.argv[1:]
    poll = int(args[args.index("--poll")+1]) if "--poll" in args else 45
    conf = float(args[args.index("--conf")+1]) if "--conf" in args else 0.30
    model_name = args[args.index("--model")+1] if "--model" in args else "yolov8x.pt"
    HITS.mkdir(parents=True, exist_ok=True)
    last_seen = 0
    if STATE.exists():
        try: last_seen = int(STATE.read_text().strip())
        except Exception: pass
    m = YOLO(model_name)
    print(f"[*] live watch: poll={poll}s conf={conf} model={model_name}", flush=True)
    while True:
        try:
            s, H = session_headers()
            base = f"https://{s.api_domain}"
            j = requests.get(base+"/v3/alarms", headers=H,
                             params={"limit":20,"offset":0,"queryType":-1}, timeout=20).json()
            al = [a for a in j.get("alarms", []) if a["alarmStartTime"] > last_seen]
            al.sort(key=lambda a: a["alarmStartTime"])
            for a in al:
                last_seen = max(last_seen, a["alarmStartTime"])
                url = a.get("picUrl")
                if not url: continue
                r = requests.get(url, headers=H, timeout=20)
                if r.status_code != 200 or len(r.content) < 1000: continue
                pd, day = day_dir(a["alarmStartTime"])
                tmp = pd / f".{a['alarmId']}.raw.jpg"; tmp.write_bytes(r.content)
                a["_thumb"] = str(tmp)
                img = cv2.imread(str(tmp))
                best = None
                if img is not None:
                    for b in m(img, verbose=False, conf=conf)[0].boxes:
                        lab = m.names[int(b.cls)]; cf = float(b.conf)
                        if lab in INTEREST and (best is None or cf > best[1]):
                            best = (lab, cf, [int(x) for x in b.xyxy[0]])
                if best:
                    a["_label"], a["_conf"], a["_box"] = best[0], round(best[1],2), best[2]
                    cv2.imwrite(str(pd / f"{a['alarmId']}.jpg"), annotate(a))
                    with open(pd / "events.jsonl", "a") as f:
                        f.write(json.dumps({k:a[k] for k in
                            ("alarmId","alarmName","channelNo","alarmStartTimeStr",
                             "alarmStartTime","_label","_conf")}) + "\n")
                    print(f"  [HIT] {a['alarmStartTimeStr']} {a['alarmName']} -> {best[0]} {best[1]:.2f}", flush=True)
                else:
                    print(f"  drop  {a['alarmStartTimeStr']} {a['alarmName']} (no person/vehicle/animal)", flush=True)
                tmp.unlink(missing_ok=True)
            STATE.write_text(str(last_seen))
        except Exception as e:
            print(f"[!] poll error: {e}", flush=True)
        time.sleep(poll)


if __name__ == "__main__":
    main()
