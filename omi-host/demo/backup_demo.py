"""Measure a running stack for backup_demo.sh: what a user sees, and what the stores hold.

  --phase measure --out before.json     record the counts of the stack that is about to "die"
  --phase compare --expect before.json  the restored stack must show the same; PASS/FAIL table

Counted through the user's own eyes (local login, the HTTP API, CalDAV with the server's
password) and through the stores (Firestore collection groups and Auth users on the
emulators, Redis keys, Radicale's files). Takes the same arguments caldav_demo.sh passes
its driver (OMI_DEMO_DRIVER=backup_demo.py), plus the ones above; the login comes from
OMI_BACKUP_DEMO_USER / OMI_BACKUP_DEMO_PASSWORD.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backup"))
import omi_backup  # noqa: E402


def call(method, url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read() or b"null")


def dav(url, user, password, depth="1"):
    auth = base64.b64encode(f"{user}:{password}".encode()).decode()
    req = urllib.request.Request(url, method="PROPFIND", headers={"Depth": depth, "Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


def caldav_items(base, user, password):
    """Calendars and their .ics items, as a phone (DAVx5) sees them over HTTP."""
    _, listing = dav(base.rstrip("/") + f"/{user}/", user, password)
    hrefs = sorted(set(re.findall(r"<(?:\w+:)?href>([^<]+/)</(?:\w+:)?href>", listing)))
    collections = [h for h in hrefs if h.rstrip("/").count("/") >= 2]
    items = 0
    for href in collections:
        _, body = dav(base.rstrip("/") + href, user, password)
        items += len(re.findall(r"<(?:\w+:)?href>[^<]+\.ics</(?:\w+:)?href>", body))
    return {"collections": len(collections), "items": items}


def measure(args):
    user, password = os.environ["OMI_BACKUP_DEMO_USER"], os.environ["OMI_BACKUP_DEMO_PASSWORD"]
    token = call("POST", args.backend + "/v1/auth/local-login", {"username": user, "password": password})
    headers = {"Authorization": f"Bearer {token['session_token']}"}
    convs = call("GET", args.backend + "/v1/conversations?limit=100&include_discarded=true", headers=headers)
    tasks = call("GET", args.backend + "/v1/action-items?limit=200", headers=headers)
    state = os.environ["OMI_BACKUP_DEMO_STATE"]
    host = "127.0.0.1"
    out = {
        "user_sees": {
            "login": True,
            "conversations": len(convs),
            "tasks": len(tasks.get("action_items") or []),
            "caldav_over_http": caldav_items(args.caldav, args.caldav_user, args.caldav_password),
        },
        "stores": {
            "firestore": omi_backup.firestore_counts(f"{host}:{os.environ['OMI_DEMO_FIRESTORE_PORT']}"),
            "auth_users": omi_backup.auth_count_live(f"{host}:{os.environ['OMI_DEMO_AUTH_PORT']}"),
            "redis_keys": omi_backup.redis_command(host, int(os.environ["OMI_DEMO_REDIS_PORT"]), "DBSIZE"),
            **omi_backup.offline_counts(state),
        },
        "caldav_password_sha256": hashlib.sha256(args.caldav_password.encode()).hexdigest(),
    }
    return out


def flatten(prefix, value, out):
    if isinstance(value, dict):
        for k, v in value.items():
            flatten(f"{prefix}.{k}" if prefix else k, v, out)
    else:
        out[prefix] = value
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--phase", choices=("measure", "compare"), default=os.environ.get("OMI_BACKUP_DEMO_PHASE", "compare"))
    p.add_argument("--out", default=os.environ.get("OMI_BACKUP_DEMO_OUT"))
    p.add_argument("--expect", default=os.environ.get("OMI_BACKUP_DEMO_EXPECT"))
    p.add_argument("--backend", default="http://127.0.0.1:8000")
    p.add_argument("--caldav", default="http://127.0.0.1:5232/")
    p.add_argument("--caldav-user", default="omi")
    p.add_argument("--caldav-password", default=os.environ.get("CALDAV_PASSWORD", ""))
    args, _ = p.parse_known_args()
    try:
        now = measure(args)
    except (urllib.error.URLError, OSError, KeyError) as e:
        print(f"measure failed: {e}", file=sys.stderr)
        return 1
    if args.out:
        with open(args.out, "w") as f:
            json.dump(now, f, indent=2, sort_keys=True)
    if args.phase == "measure":
        print(json.dumps(now, indent=2, sort_keys=True))
        return 0
    with open(args.expect) as f:
        before = json.load(f)
    a, b = flatten("", before, {}), flatten("", now, {})
    failed = 0
    width = max(len(k) for k in a)
    print(f"\n{'':6}{'what':<{width}}  {'before':>10}  {'restored':>10}")
    for key in sorted(a):
        ok = a[key] == b.get(key)
        failed += not ok
        shown_a = "same" if key.endswith("sha256") else a[key]
        shown_b = ("same" if ok else "DIFFERENT") if key.endswith("sha256") else b.get(key)
        print(f"{'PASS' if ok else 'FAIL':<6}{key:<{width}}  {str(shown_a):>10}  {str(shown_b):>10}")
    print(f"\n{len(a) - failed} same, {failed} different")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
