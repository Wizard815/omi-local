#!/usr/bin/env python3
"""Repoint a self-hosted login username at the uid that actually owns the data.

Why this exists: `scripts/seed_local_account.py` binds a username to whatever
uid the Auth emulator hands back for `<username>@local.omi`. If that emulator
account already existed (e.g. recreated after an emulator reset), the seed
re-affirms a *different*, usually empty uid — so the user logs in successfully
and sees a blank account while their conversations/memories sit under the old
uid. The seed script cannot fix that: it only writes the uid the emulator gave
it. This script does the opposite — it takes the uid you KNOW holds the data
and points the login's Firestore record at it.

Only the Firestore `local_login_accounts/<username>` document is touched (the
uid the `/v1/auth/local-login` remote path mints tokens for). It does not move
or delete any conversations or memories.

Run inside the backend container from /app/backend:

    docker exec -it omi-local bash -c "cd /app/backend && PYTHONPATH=/app/backend:/app/backend/scripts python scripts/merge_local_account.py --username blake --target-uid <UID>"

Use --list to print every login mapping first and pick the right target.
"""

import argparse

from _container_env import inherit_pid1_env

# Container-only deps are imported lazily inside main(), after argparse has run,
# so `--help` works from anywhere (and outside the container the import error
# names the real problem instead of being masked by an argument error).

_LOCAL_ACCOUNTS_COLLECTION = 'local_login_accounts'


def _list_accounts() -> None:
    from database._client import get_firestore_client

    client = get_firestore_client()
    docs = list(client.collection(_LOCAL_ACCOUNTS_COLLECTION).stream())
    if not docs:
        print('(no local login accounts defined)')
        return
    print(f'{len(docs)} local login account(s):')
    for doc in docs:
        data = doc.to_dict() or {}
        uid = data.get('uid', '?')
        print(f'  username={doc.id!r}  uid={uid}')


def _count_data(uid: str) -> tuple[int, int]:
    """Return (conversations, memories) for a uid — the two collections that
    make the difference visible in the app immediately."""
    from database._client import get_firestore_client

    client = get_firestore_client()
    user_ref = client.collection('users').document(uid)
    convs = len(list(user_ref.collection('conversations').limit(1000).stream()))
    mems = len(list(user_ref.collection('memories').limit(1000).stream()))
    return convs, mems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--list', action='store_true', help='List all login mappings and exit (no password needed).')
    parser.add_argument('--username', help='Login username whose mapping should change (e.g. blake).')
    parser.add_argument('--target-uid', help='uid that owns the data — the mapping is repointed here.')
    parser.add_argument('--password', help='New password. Omit to leave the existing password unchanged.')
    args = parser.parse_args()

    # Load the container's real environment (FIRESTORE_EMULATOR_HOST etc.) from
    # PID 1 BEFORE importing anything that resolves a Firestore client — a fresh
    # `docker exec` shell does not inherit the entrypoint's exports.
    inherit_pid1_env()

    if args.list:
        _list_accounts()
        return

    if not args.username or not args.target_uid:
        parser.error('--username and --target-uid are required (or use --list).')

    from utils.local_auth import get_local_account, set_local_account

    username = args.username.strip().lower()
    existing = get_local_account(username)

    print(f'username      : {username}')
    print(f'current uid   : {existing.uid if existing else "(none)"}')
    print(f'target uid    : {args.target_uid}')
    if existing and existing.uid == args.target_uid:
        print('already points at the target uid — nothing to change.')

    convs, mems = _count_data(args.target_uid)
    print(f'target data   : {convs} conversations, {mems} memories')

    if not existing:
        parser.error(f'No local login account {username!r} exists. Create it first with seed_local_account.py.')

    if args.password:
        set_local_account(username, args.target_uid, args.password)
        print(f"repointed {username!r} -> {args.target_uid} (password updated)")
    else:
        # Reuse the existing hash: re-writing the doc with the same password is
        # the only supported shape of set_local_account, so we cannot change the
        # uid without a password argument. Fail loudly rather than silently
        # resetting to an unknown password.
        parser.error(
            'A --password is required to repoint: set_local_account always writes a fresh '
            'password hash, so omitting it would lock the account. Re-run with --password.'
        )

    after = get_local_account(username)
    print(f'verified      : {username!r} now resolves to {after.uid if after else "(missing)"}')


if __name__ == '__main__':
    main()
