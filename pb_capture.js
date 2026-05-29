// Frida 17: capture the Hik-Connect playback protocol.
//  - native StreamInfoReq::set_streamurl  -> the exact /playback VTM URL
//  - Java NativeApi.startPlayback(long,String,String,String) -> begin/end times
//  - CASClient_SearchRecordFile -> the record-search step (if used)

const LIB = "libezstreamclient.so";
const SET_URL = "_ZN3hik2ys14streamprotocol13StreamInfoReq13set_streamurlEPKc";

function mod() { return Process.findModuleByName(LIB); }

function hookNative() {
  const m = mod();
  const a = m.findExportByName(SET_URL);
  if (a) {
    Interceptor.attach(a, {
      onEnter(args) {
        try { send("[set_streamurl] " + args[1].readUtf8String()); } catch (e) {}
      },
    });
    send("[+] hooked set_streamurl");
  } else send("[!] set_streamurl not found");

  for (const sym of ["CASClient_SearchRecordFile", "CASClient_SearchRecordFileEx",
                     "CASClient_SearchRecordByMounth", "_Z22ezstream_startPlaybackPvPaS0_S0_"]) {
    const p = m.findExportByName(sym);
    if (p) {
      Interceptor.attach(p, {
        onEnter(args) {
          let extra = "";
          if (sym.indexOf("ezstream_startPlayback") >= 0) {
            try { extra = " a=" + args[1].readUtf8String() + " b=" + args[2].readUtf8String() + " c=" + args[3].readUtf8String(); } catch (e) {}
          }
          send("[native] " + sym + extra);
        },
      });
      send("[+] hooked " + sym);
    }
  }
}

function hookJava() {
  Java.perform(function () {
    try {
      const NA = Java.use("com.ez.stream.NativeApi");
      NA.startPlayback.overloads.forEach(function (ov) {
        ov.implementation = function () {
          const a = Array.prototype.slice.call(arguments);
          send("[Java startPlayback] args=" + JSON.stringify(a.map(String)));
          return ov.apply(this, arguments);
        };
      });
      send("[+] hooked Java NativeApi.startPlayback (" + NA.startPlayback.overloads.length + " overloads)");
    } catch (e) { send("[!] java hook err: " + e); }
  });
}

if (mod()) { hookNative(); } else {
  send("[*] waiting for " + LIB + "…");
  const iv = setInterval(function () { if (mod()) { clearInterval(iv); hookNative(); } }, 200);
}
setTimeout(hookJava, 1500);
send("[*] playback capture armed");
