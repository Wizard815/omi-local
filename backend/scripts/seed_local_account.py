#!/usr/bin/env python3
"""Create (or update the password of) the single login account for a
self-hosted OMI deployment: the LAN-only Firebase Auth emulator account, and
(unless --lan-only) the internet-reachable credential used by /v1/auth/local-login.

There is no in-app account-creation flow — this is the server-side
counterpart to both the app's LAN login screen (server IP + username +
password, talks to the Auth emulator directly) and the remote login flow
(server URL + username + password, talks to /v1/auth/local-login, see
utils/local_auth.py for why that second path exists). Run inside the backend
container, from /app/backend with PYTHONPATH set, not the repo root — a
plain `docker exec -it omi-local python backend/scripts/seed_local_account.py`
run from /app fails with `ModuleNotFoundError: No module named 'utils'`
once it reaches the deferred set_local_account import below, because
Python's sys.path[0] becomes the SCRIPT's own directory (backend/scripts),
not /app/backend:

    docker exec -it omi-local bash -c "cd /app/backend && PYTHONPATH=/app/backend python scripts/seed_local_account.py --username you"

The username is mapped to a synthetic "<username>@local.omi" address for the
emulator account — the same transform the app applies (see
AuthService.usernameToLocalEmail in app/lib/services/auth_service.dart) —
Firebase Auth's password provider requires an email-shaped identifier, but
nothing about it is ever shown to the user or treated as a real email.

The emulator account is created directly against the Auth emulator's
Identity Toolkit REST API (the same mechanism
scripts/dev-harness/dev_harness/memory_scenarios.py uses for seeded test
users). Re-running with the same username updates the password on both
accounts instead of failing, so this doubles as a password-reset tool.
Nothing here talks to any Omi-operated service, or Firebase's/Google's real
servers — the remote credential is a session token this backend signs itself
(see utils/local_auth.py, LOCAL_AUTH_JWT_SECRET), not a Firebase custom token.
"""

import argparse
import getpass
import json
import urllib.error
import urllib.request

# entrypoint.sh (PID 1 in the container) exports these at runtime, but a
# fresh `docker exec` session doesn't inherit a sibling process's exports —
# see _container_env.py for why reading them from /proc/1/environ beats
# hardcoding a copy of entrypoint.sh's list here.
from _container_env import inherit_pid1_env

inherit_pid1_env()

DEFAULT_AUTH_PORT = 9099


def username_to_local_email(username: str) -> str:
    return f"{username.strip().lower()}@local.omi"


def _post(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Request to {url} failed: HTTP {exc.code} {body}") from exc


def seed_account(auth_host: str, auth_port: int, username: str, password: str) -> str:
    email = username_to_local_email(username)
    base = f"http://{auth_host}:{auth_port}/identitytoolkit.googleapis.com/v1/accounts"
    key = "local-dev-harness"  # the emulator does not validate this key

    # EMAIL_EXISTS is an expected outcome on a re-run (this doubles as a
    # password-reset tool, per the module docstring), not a fatal error —
    # _post() raises SystemExit on any non-2xx response, so it can't be used
    # here directly; catch the emulator's 400 and fall through to sign-in.
    try:
        sign_up = _post(
            f"{base}:signUp?key={key}",
            {"email": email, "password": password, "returnSecureToken": True},
        )
    except SystemExit as exc:
        if "EMAIL_EXISTS" not in str(exc):
            raise
        sign_up = {}

    if "idToken" in sign_up:
        uid = sign_up["localId"]
        _post(
            f"{base}:update?key={key}",
            {"idToken": sign_up["idToken"], "displayName": username, "returnSecureToken": False},
        )
        print(f"Created account '{username}' (uid={uid})")
        return uid

    # EMAIL_EXISTS: update the password on the existing account instead.
    sign_in = _post(
        f"{base}:signInWithPassword?key={key}",
        {"email": email, "password": password, "returnSecureToken": True},
    )
    if "idToken" not in sign_in:
        raise SystemExit(f"Could not sign in as '{username}' to update it; response: {sign_in}")
    uid = sign_in["localId"]
    print(f"Account '{username}' already exists (uid={uid}); password confirmed/updated.")
    return uid


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--username", required=True, help="Login username for the app (no email needed)")
    parser.add_argument(
        "--password",
        help="Login password. Omit to be prompted (recommended, keeps it out of shell history).",
    )
    parser.add_argument("--auth-host", default="127.0.0.1", help="Firebase Auth emulator host (default: 127.0.0.1)")
    parser.add_argument("--auth-port", type=int, default=DEFAULT_AUTH_PORT, help=f"default: {DEFAULT_AUTH_PORT}")
    parser.add_argument(
        "--lan-only",
        action="store_true",
        help="Skip the remote-login credential (Firestore + argon2 hash) — only seed the LAN emulator account.",
    )
    args = parser.parse_args()

    password = args.password or getpass.getpass("Password: ")
    if len(password) < 8:
        raise SystemExit("Password must be at least 8 characters (Firebase Auth's minimum).")

    uid = seed_account(args.auth_host, args.auth_port, args.username, password)

    if not args.lan_only:
        # Imported here, not at module level: these pull in the backend's full
        # dependency set (firebase_admin, google-cloud-firestore), so running
        # this script with --lan-only stays usable outside the container too.
        from utils.local_auth import set_local_account

        set_local_account(args.username, uid, password)
        print(f"Remote-login credential stored for '{args.username}' (uid={uid}).")

    print(f"On the phone: Server IP = this machine's LAN IP, Username = {args.username}.")


if __name__ == "__main__":
    main()
