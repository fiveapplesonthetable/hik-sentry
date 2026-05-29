#!/usr/bin/env python3
"""Test reading the DVR's OWN recorded storage via VTM playback.

The EZVIZ/Hik VTM supports /playback?...&begin=&end= (ISO YYYY-MM-DDTHH:MM:SS),
reusing the exact live-stream handshake we already cracked. If this works we can
process the camera's past recordings on demand — no local 24/7 capture needed.

Usage: playback_test.py <channel> <begin ISO> <end ISO> <out.h264>
  e.g. playback_test.py 1 2026-05-29T16:30:00 2026-05-29T16:35:00 pb.h264
"""
import sys, time, socket, uuid, pathlib
from urllib.parse import urlparse

# import the cracked live-stream machinery from hik.py
HIK = pathlib.Path("/mnt/agent/hikvision_e2e/scripts")
sys.path.insert(0, str(HIK))
import hik  # noqa


def playback_connect(sess, serial, channel, begin, end, timeout=10.0):
    vtm_info = sess.vtm(serial, channel)
    host, port = vtm_info["externalIp"], int(vtm_info["port"])
    token = sess.token_batch(count=5)[0]
    lid = str(uuid.uuid4())

    def build_body(url, vtmkey=""):
        parts = [hik._proto_string(1, url)]
        if vtmkey:
            parts.append(hik._proto_string(2, vtmkey))
        parts += [hik._proto_string(3, hik.UA), hik._proto_varint(4, 0),
                  hik._proto_string(5, ""), hik._proto_string(6, hik.UA)]
        return b"".join(parts)

    # Step 1 — /playback instead of /live, with &begin=&end=
    url1 = (f"ysproto://{host}:{port}/playback?dev={serial}&chn={channel}"
            f"&stream=1&cln={hik.CLN_TYPE}&isp=0&auth=1&ssn={token}"
            f"&lid={lid}&biz=1&begin={begin}&end={end}")
    print(f"[pb] step1 URL: {url1}")
    s1 = socket.create_connection((host, port), timeout=timeout); s1.settimeout(timeout)
    s1.sendall(hik.vtm_encode(build_body(url1), channel=hik.CH_MESSAGE,
                              mcode=hik.MCODE_STREAMINFO_REQ, seq=1))
    pkt = hik.recv_packet(s1); s1.close()
    if pkt is None:
        raise RuntimeError("step1: no response")
    info1 = hik._parse_stream_info_rsp(pkt[1])
    print(f"[pb] step1 result={info1.result} streamurl={info1.streamurl}")
    if info1.result != 5302:
        raise RuntimeError(f"step1 unexpected result {info1.result}")

    p = urlparse(info1.streamurl)
    url2 = info1.streamurl + f"&timestamp={int(time.time()*1000)}"
    s2 = socket.create_connection((p.hostname, p.port), timeout=timeout); s2.settimeout(timeout)
    s2.sendall(hik.vtm_encode(build_body(url2, info1.vtmstreamkey),
                              channel=hik.CH_MESSAGE, mcode=hik.MCODE_STREAMINFO_REQ, seq=2))
    pkt = hik.recv_packet(s2)
    info2 = hik._parse_stream_info_rsp(pkt[1])
    print(f"[pb] step2 result={info2.result}")
    if info2.result != 0:
        s2.close(); raise RuntimeError(f"step2 unexpected result {info2.result}")
    return hik.StreamHandle(socket=s2, datakey=info2.datakey or 0,
                            streamssn=info2.streamssn, streamhead=info2.streamhead,
                            serverinfo=info2.serverinfo)


def main():
    ch = int(sys.argv[1]); begin = sys.argv[2]; end = sys.argv[3]
    out = sys.argv[4] if len(sys.argv) > 4 else "pb.h264"
    sess = hik.HikSession.load()
    serial = hik._first_serial(sess)
    print(f"[pb] device {serial} ch{ch}  {begin} .. {end}")
    h = playback_connect(sess, serial, ch, begin, end)
    print("[pb] connected — draining packets")
    fout = open(out, "wb"); nals = 0; t0 = time.time()
    h.socket.settimeout(8)

    def rtp_iter():
        for _hdr, body in hik.stream_drain(h):
            if time.time() - t0 > 30:
                break
            f = hik.parse_rtp(body)
            if f is not None:
                yield f
    try:
        for nal in hik.h264_depacketize(rtp_iter()):
            fout.write(nal); nals += 1
    except (socket.timeout, TimeoutError):
        pass
    fout.close()
    print(f"[pb] {nals} NALs, {pathlib.Path(out).stat().st_size} bytes -> {out}")


if __name__ == "__main__":
    main()
