#!/usr/bin/env python3
"""Create (or update the password of) the single login account for a
self-hosted OMI deployment, in the Firebase Auth emulator.

This is the server-side counterpart to the app's login screen (server IP +
username + password) — there is no in-app account-creation flow. Run this
once against your running dev-harness / self-hosted stack:

    python backend/scripts/seed_local_account.py --username you

The username is mapped to a synthetic "<username>@local.omi" address, the
same transform the app applies (see AuthService.usernameToLocalEmail in
app/lib/services/auth_service.dart) — Firebase Auth's password provider
requires an email-shaped identifier, but nothing about it is ever shown to
the user or treated as a real email.

The account is created directly against the Auth emulator's Identity Toolkit
REST API (the same mechanism scripts/dev-harness/dev_harness/memory_scenarios.py
uses for seeded test users), so it needs no real Firebase project or network
access. Re-running with the same username updates the password instead of
failing, so this doubles as a password-reset tool. Nothing here talks to any
Omi-operated service.
"""

import argparse
import getpass
import json
import sys
import urllib.error
import urllib.request

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

    sign_up = _post(
        f"{base}:signUp?key={key}",
        {"email": email, "password": password, "returnSecureToken": True},
    )
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
    args = parser.parse_args()

    password = args.password or getpass.getpass("Password: ")
    if len(password) < 8:
        raise SystemExit("Password must be at least 8 characters (Firebase Auth's minimum).")

    seed_account(args.auth_host, args.auth_port, args.username, password)
    print(f"On the phone: Server IP = this machine's LAN IP, Username = {args.username}.")


if __name__ == "__main__":
    main()
