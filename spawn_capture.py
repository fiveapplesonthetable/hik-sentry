import sys, time, frida
dev = frida.get_device(sys.argv[1]); src=open(sys.argv[2]).read()
pid = dev.spawn(["com.hikvision.hikconnect"])
s = dev.attach(pid); sc = s.create_script(src)
sc.on("message", lambda m,d: print(m.get("payload","") if m["type"]=="send" else "ERR", flush=True))
sc.load(); dev.resume(pid)
print(f"[*] spawned {pid}", flush=True); time.sleep(int(sys.argv[3]))
