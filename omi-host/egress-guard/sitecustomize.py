"""Local-only egress guard for the omi-local stack's Python processes.

Loaded automatically by every Python process that has this folder on PYTHONPATH
(Python imports `sitecustomize` at startup). It watches name lookups and socket
connects and, depending on OMI_EGRESS_GUARD:

  block (default in omi-host) - refuse any destination that isn't loopback or a
        private LAN address, and log it
  log   - allow everything, log what would have been refused
  off   - do nothing

OMI_EGRESS_ALLOW adds destinations on purpose (comma-separated host names,
IPs or CIDRs), e.g. when you deliberately use a cloud key:
  OMI_EGRESS_ALLOW=api.openai.com
Link-local addresses (169.254.0.0/16, where cloud metadata servers live) are
refused unless listed explicitly. Log lines go to stderr and, when set, to
OMI_EGRESS_LOG (JSON lines), with the code that made the call.

While on, the guard also:
- turns uvloop off (uvicorn then uses the standard asyncio loop): uvloop
  resolves names and opens sockets in native code this guard can't see;
- drops HTTP(S)_PROXY / ALL_PROXY from the environment, because a proxy on a
  LAN address would carry any request out past the checks
  (OMI_EGRESS_KEEP_PROXY=1 keeps them).

What this can't see: native code that opens its own sockets (gRPC's C core,
used here only for the Firestore emulator; the emulator's JVM; firebase-tools'
Node). For a hard guarantee put the stack behind the network rules in
omi-host/NETWORK.md as well.
"""

import ipaddress
import json
import os
import socket
import sys
import time
import traceback

_MODE = os.environ.get("OMI_EGRESS_GUARD", "off").strip().lower()


def _allow_rules():
    names, nets = set(), []
    for part in os.environ.get("OMI_EGRESS_ALLOW", "").split(","):
        part = part.strip().lower()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            names.add(part.rstrip("."))
    return names, nets


def _local_ip(ip):
    if getattr(ip, "ipv4_mapped", None) is not None:
        ip = ip.ipv4_mapped
    if ip.is_link_local:
        return False  # metadata servers: never by default
    return ip.is_loopback or ip.is_private


def _allowed(host, ip):
    names, nets = _allow_rules()
    h = (host or "").lower().rstrip(".")
    if h and any(h == n or h.endswith("." + n) for n in names):
        return True
    if ip is not None:
        if any(ip.version == n.version and ip in n for n in nets):
            return True
        return _local_ip(ip)
    return False


def _caller():
    """The first frame in the app's own code, plus the library that made the call."""
    frames = traceback.extract_stack()[:-3]
    app, lib = None, None
    for f in reversed(frames):
        fn = f.filename
        if "sitecustomize" in fn:
            continue
        if ("site-packages" in fn or "dist-packages" in fn) and lib is None:
            lib = fn.split("site-packages/")[-1].split("dist-packages/")[-1]
            lib = f"{lib}:{f.lineno}"
        elif "/lib/python" not in fn and "site-packages" not in fn and app is None and not fn.startswith("<"):
            app = f"{fn}:{f.lineno}"
        if app and lib:
            break
    return app, lib


def _report(kind, host, port, decision):
    app, lib = _caller()
    rec = {"t": round(time.time(), 3), "kind": kind, "host": host, "port": port, "decision": decision,
           "pid": os.getpid(), "proc": os.path.basename(sys.argv[0] if sys.argv else "?"), "code": app, "lib": lib}
    line = json.dumps(rec)
    try:
        sys.stderr.write(f"[egress-guard] {line}\n")
    except Exception:
        pass
    path = os.environ.get("OMI_EGRESS_LOG")
    if path:
        try:
            with open(path, "a") as f:
                f.write(line + "\n")
        except Exception:
            pass


def _parse_ip(s):
    try:
        return ipaddress.ip_address(str(s).split("%")[0])
    except ValueError:
        return None


if _MODE in ("block", "log"):
    # Native event loop: invisible to the checks below, so it doesn't load at all.
    if os.environ.get("OMI_EGRESS_ALLOW_UVLOOP") != "1":
        sys.modules["uvloop"] = None
    if os.environ.get("OMI_EGRESS_KEEP_PROXY") != "1":
        for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            os.environ.pop(_k, None)

    _real_getaddrinfo = socket.getaddrinfo
    _real_connect = socket.socket.connect
    _real_connect_ex = socket.socket.connect_ex

    def getaddrinfo(host, port, *args, **kwargs):
        if host is None:
            return _real_getaddrinfo(host, port, *args, **kwargs)
        h = host.decode() if isinstance(host, bytes) else str(host)
        try:
            res = _real_getaddrinfo(host, port, *args, **kwargs)
        except socket.gaierror:
            # A public name the resolver can't answer (an air-gapped host): still an attempt to reach out.
            if _parse_ip(h) is None and "." in h and not _allowed(h, None):
                _report("dns", h, port, "unresolved")
            raise
        ips = [_parse_ip(r[4][0]) for r in res if r[0] in (socket.AF_INET, socket.AF_INET6)]
        if ips and not all(_allowed(h, ip) for ip in ips if ip is not None):
            if _MODE == "block":
                _report("dns", h, port, "blocked")
                raise socket.gaierror(socket.EAI_NONAME, f"omi egress guard: {h} is outside the local network (OMI_EGRESS_ALLOW to permit)")
            _report("dns", h, port, "would-block")
        return res

    def _check(sock, address):
        if sock.family not in (socket.AF_INET, socket.AF_INET6) or not isinstance(address, tuple):
            return
        ip = _parse_ip(address[0])
        if ip is None or _allowed(None, ip):
            return
        if _MODE == "block":
            _report("connect", str(ip), address[1], "blocked")
            raise ConnectionRefusedError(f"omi egress guard: {ip} is outside the local network (OMI_EGRESS_ALLOW to permit)")
        _report("connect", str(ip), address[1], "would-block")

    def connect(self, address):
        _check(self, address)
        return _real_connect(self, address)

    def connect_ex(self, address):
        _check(self, address)
        return _real_connect_ex(self, address)

    socket.getaddrinfo = getaddrinfo
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
