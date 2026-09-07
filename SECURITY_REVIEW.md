# Hik-Connect (com.hikvision.hikconnect) v4.19.0 — Security Summary

## A note on scope and disclosure

This is a **findings-level** summary from a static review of the app, done on
**owner-operated equipment** while reverse-engineering the streaming protocol for
interoperability. It deliberately **omits the exploit-level specifics** — exact
function offsets, key material, disassembly, and step-by-step reproduction — that
were in the private working notes.

The intent is to describe the *classes* of weakness and why they matter, not to
hand anyone a working exploit. None of this is a novel zero-day: the patterns
(skipped certificate validation, hardcoded keys, weak custom integrity) are
well-known anti-patterns. No dynamic/live exploitation was performed. Issues of
this kind belong with the vendor.

## The shape of it

The app **bundles its own SSL/crypto stack** — several renamed, end-of-life
OpenSSL builds plus a custom C wrapper — instead of relying on the platform's
continuously-patched TLS. Most of the problems live in that home-grown layer.

### 1. Certificate validation is inconsistent, and absent on the streaming path (HIGH)

The native TLS path used for cloud video streaming completes the TLS session
**without validating the server certificate** — no peer verification, no chain or
hostname check. Separately, two Java / React-Native paths install trust-all /
ignore-hostname handlers. So a network-adjacent attacker (rogue Wi-Fi, ARP/DNS
spoofing, a malicious upstream) can impersonate the cloud, sit in the middle of
the stream, and capture the session token, the per-device key, and the stream key
— and, through login, the account password. Some sub-channels *do* pin
certificates, so it is inconsistent rather than uniformly absent, which is
arguably worse because it looks protected.

### 2. Bundled end-of-life crypto libraries (HIGH)

The app ships multiple OpenSSL builds (1.0.2- and 1.1.1-era) and an old mbedTLS,
all past end-of-life. Because they live inside the app, **OS security updates
cannot reach them** — only an app update can. They carry a range of known,
since-fixed CVEs (for example a certificate-parsing denial-of-service and later
X.509 type-confusions), some reachable wherever the app parses an attacker-supplied
certificate — which finding #1 makes easy to feed in.

### 3. Hardcoded / static keys in the custom wrapper (HIGH / MED)

The custom crypto wrapper uses **fixed, in-binary key material** for its generic
AES and DES helpers, and a separate "key protection" component is itself built on
a static embedded key. Anything protected this way is recoverable by anyone with
the APK — security by obscurity, not real protection.

### 4. Weak, forgeable integrity in the bespoke envelope (MED → HIGH)

The proprietary streaming envelope authenticates data with an HMAC computed **over
a CRC32** of the data, not over the data itself. CRC32 is linear and not
collision-resistant, so a tampered body can be crafted that still passes the
integrity check without knowing the key. The envelope also wraps the session
secret in ECB mode.

### 5. Replayable request signature (MED)

The streaming request signature is a **static HMAC over the URL, keyed by the
device's long-term key** with no per-session salt — so the same request yields the
same signature and is replayable. A server-side check appears to be the main thing
preventing a pure-software replay: a single server-side control rather than defense
in depth.

### 6. Weak defaults (LOW – MED)

Media-stream encryption is **optional** (plaintext video is a supported mode) and
the datakey integrity check is plain MD5. The global network policy permits
cleartext HTTP and pins nothing by default; app backup is enabled.

## Why the app bundles its own crypto, and the takeaway

Three reasons are evident, and all are net-negative for security: multiple legacy
SDKs each expect a specific OpenSSL major, so they ship side-by-side under renamed
names; a custom C wrapper provides the vendor's own crypto API (and is where the
skipped validation and fixed keys live); and cross-platform parity is prioritized
over the platform's patched TLS.

The net effect is crypto **frozen years in the past, unpatchable by the OS, wrapped
in code that skips certificate validation and embeds fixed keys.** A better design
would use the platform TLS (with optional pinning) and reserve bundled crypto only
for the genuine device-side protocol — with real authenticated encryption,
per-session keys, and a cryptographic (not CRC32) MAC.

---

*Exploit-level specifics — exact offsets, key material, assembly, and reproduction
steps — are intentionally withheld from this public summary.*
