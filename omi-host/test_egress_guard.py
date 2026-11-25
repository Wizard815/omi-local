"""egress-guard/sitecustomize.py: local destinations pass, everything else is refused and logged.

Each case runs a fresh Python with the guard on PYTHONPATH (sitecustomize only loads at
startup). Name lookups are faked inside the child so nothing touches the network.
Run: python -m pytest omi-host/test_egress_guard.py
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

GUARD = Path(__file__).with_name("egress-guard")

# Inside the child: answer lookups from a table instead of DNS, and report each outcome.
PRELUDE = textwrap.dedent(
    """
    import socket, json, sys
    FAKE = {"api.openai.com": "104.18.7.192", "llama.lan": "192.168.1.5", "nas.example.com": "192.168.1.7",
            "metadata.google.internal": "169.254.169.254"}
    import sitecustomize as g
    def fake(host, port, *a, **k):
        h = host.decode() if isinstance(host, bytes) else host
        if h in FAKE:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (FAKE[h], port or 0))]
        if h == "unknown.example":
            raise socket.gaierror(socket.EAI_NONAME, "no such host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (h, port or 0))]
    if hasattr(g, "_real_getaddrinfo"):
        g._real_getaddrinfo = fake
    else:
        socket.getaddrinfo = fake
    def try_lookup(h):
        try:
            socket.getaddrinfo(h, 443); return "ok"
        except socket.gaierror as e:
            return "refused" if "egress guard" in str(e) else "unresolved"
    def try_connect(ip, port=9):
        s = socket.socket(); s.settimeout(0.2)
        try:
            s.connect((ip, port)); return "connected"
        except ConnectionRefusedError as e:
            return "refused" if "egress guard" in str(e) else "passed"
        except OSError:
            return "passed"  # the guard let it through; nothing listens there
        finally:
            s.close()
    """
)


def run(body, tmp_path, **env):
    log = tmp_path / "egress.jsonl"
    e = {k: v for k, v in os.environ.items() if not k.startswith("OMI_EGRESS")}
    e.update({"PYTHONPATH": str(GUARD), "OMI_EGRESS_LOG": str(log), **env})
    out = subprocess.run([sys.executable, "-c", PRELUDE + textwrap.dedent(body)], env=e, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    lines = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return out.stdout.strip(), lines


def test_block_mode_refuses_public_and_metadata_allows_lan(tmp_path):
    out, log = run(
        """
        print(json.dumps([try_lookup("api.openai.com"), try_lookup("llama.lan"), try_lookup("nas.example.com"),
                          try_lookup("metadata.google.internal"), try_connect("8.8.8.8"), try_connect("127.0.0.1"),
                          try_connect("192.168.1.5"), try_connect("169.254.169.254")]))
        """,
        tmp_path,
        OMI_EGRESS_GUARD="block",
    )
    assert json.loads(out) == ["refused", "ok", "ok", "refused", "refused", "passed", "passed", "refused"]
    blocked = {(r["kind"], r["host"]) for r in log if r["decision"] == "blocked"}
    assert blocked == {("dns", "api.openai.com"), ("dns", "metadata.google.internal"), ("connect", "8.8.8.8"), ("connect", "169.254.169.254")}


def test_allow_list_by_name_and_cidr(tmp_path):
    out, _ = run(
        'print(json.dumps([try_lookup("api.openai.com"), try_connect("8.8.8.8"), try_connect("1.1.1.1")]))',
        tmp_path,
        OMI_EGRESS_GUARD="block",
        OMI_EGRESS_ALLOW="openai.com, 8.8.8.0/24",
    )
    assert json.loads(out) == ["ok", "passed", "refused"]


def test_log_mode_lets_through_but_records(tmp_path):
    out, log = run('print(json.dumps([try_lookup("api.openai.com"), try_connect("8.8.8.8")]))', tmp_path, OMI_EGRESS_GUARD="log")
    assert json.loads(out) == ["ok", "passed"]
    assert {r["decision"] for r in log} == {"would-block"}


def test_unresolvable_public_names_are_still_recorded(tmp_path):
    out, log = run('print(try_lookup("unknown.example"))', tmp_path, OMI_EGRESS_GUARD="block")
    assert out == "unresolved"
    assert log[0]["host"] == "unknown.example" and log[0]["decision"] == "unresolved"


def test_off_by_default_changes_nothing(tmp_path):
    out, log = run('print(try_lookup("api.openai.com"))', tmp_path)
    assert out == "ok" and log == []


def test_uvloop_off_and_proxy_env_dropped_while_on(tmp_path):
    out, _ = run(
        """
        import os, importlib
        try:
            importlib.import_module("uvloop"); loop = "uvloop"
        except ImportError:
            loop = "asyncio"
        print(json.dumps([loop, os.environ.get("HTTPS_PROXY"), os.environ.get("NO_PROXY")]))
        """,
        tmp_path,
        OMI_EGRESS_GUARD="block",
        HTTPS_PROXY="http://192.168.1.2:3128",
        NO_PROXY="localhost",
    )
    assert json.loads(out) == ["asyncio", None, "localhost"]
