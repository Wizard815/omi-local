#!/usr/bin/env python3
"""Register a self-hosted chat-tool app in this deployment's local
Firestore, so it shows up in the app browser without going through Omi's
real cloud approval pipeline.

Bypasses POST /v1/apps intentionally rather than calling it: that endpoint
requires a multipart image upload and rejects an app with neither
external_integration.triggers_on nor actions (both correct for a real
webhook/action app, both wrong for a pure chat-tools app like these). This
script builds the same AppCreate shape that endpoint validates against and
reuses its actual manifest-fetch logic (routers.apps._process_chat_tools_manifest)
instead of reimplementing it, so tool definitions are populated the same way
a real app creation would.

Run inside the backend container, same as seed_local_account.py — from
/app/backend with PYTHONPATH set, not the repo root: a plain
`docker exec -it omi-local python backend/scripts/register_chat_apps.py`
run from /app fails with `ModuleNotFoundError: No module named 'database'`,
because Python's sys.path[0] becomes the SCRIPT's own directory
(backend/scripts), not /app/backend, so the `database`/`models`/`routers`
packages this script imports aren't on the path:

    docker exec -it omi-local bash -c "cd /app/backend && PYTHONPATH=/app/backend python scripts/register_chat_apps.py"

With no arguments, registers the built-in default list (the four
gateway-mounted apps plus the Hermes Agent bridge). To register ONE new app
without editing this file — e.g. after adding it to OMI_APPS_LIST and
restarting omi-apps-gateway (see docker-compose.yml) — pass it directly:

    docker exec -it omi-local bash -c "cd /app/backend && PYTHONPATH=/app/backend python scripts/register_chat_apps.py \\
        --slug local-newapp --name 'New App' --category utilities \\
        --description 'What it does.' --mount newapp"

--mount assumes the app is gateway-mounted (http://omi-apps-gateway:8080/<mount>);
for a standalone container instead (like Hermes Agent), pass --base-url
directly (e.g. http://omi-app-something:8000) instead of --mount.

Idempotent: re-running overwrites each app's document in place (upsert_app_to_db
uses Firestore .set(), not .add()), so it doubles as a "re-fetch manifests"
tool after you change/redeploy one of these services.
"""

import argparse

# entrypoint.sh (PID 1 in the container) exports these at runtime, but a
# fresh `docker exec` session doesn't inherit a sibling process's exports —
# see _container_env.py for why reading them from /proc/1/environ beats
# hardcoding a copy of entrypoint.sh's list here. This script's import chain
# is deep (routers.apps -> ... -> database.conversations -> utils.encryption,
# which hard-fails at import time without ENCRYPTION_SECRET), so it needs
# more of entrypoint.sh's exports than just the Firestore/Auth ones.
from _container_env import inherit_pid1_env

inherit_pid1_env()

# base_url must be reachable from the omi-local container. The gateway-mounted
# apps share one container (omi-apps-gateway, see omi-host/apps-gateway/) and
# are distinguished only by mount path; Hermes Agent is its own separate
# container. Either way, this relies on Compose's per-network DNS (same
# container name as the compose service), so these only resolve once
# omi-local and these app services share a network (see
# docker-compose.override.yml.example's omi-apps section — a service not on
# the same network can't be reached by name even though it's running).
GATEWAY = 'http://omi-apps-gateway:8080'

DEFAULT_APPS = [
    {
        'slug': 'local-wikipedia',
        'name': 'Wikipedia',
        'description': 'Search Wikipedia articles and fetch summaries during a conversation.',
        'category': 'utilities',
        'base_url': f'{GATEWAY}/wikipedia',
    },
    {
        'slug': 'local-open-library',
        'name': 'Open Library',
        'description': 'Search books, fetch metadata, and browse subject recommendations.',
        'category': 'utilities',
        'base_url': f'{GATEWAY}/open-library',
    },
    {
        'slug': 'local-open-meteo',
        'name': 'Weather (Open-Meteo)',
        'description': 'Current weather, short forecasts, and air-quality readings.',
        'category': 'utilities',
        'base_url': f'{GATEWAY}/open-meteo',
    },
    {
        'slug': 'local-openfoodfacts',
        'name': 'Open Food Facts',
        'description': 'Look up packaged food nutrition, ingredients, and allergens by name or barcode.',
        'category': 'health-and-fitness',
        'base_url': f'{GATEWAY}/openfoodfacts',
    },
    {
        'slug': 'local-hermes-agent',
        'name': 'Hermes Agent',
        'description': 'Ask Hermes Agent (self-hosted, on a separate machine) a question from Omi chat.',
        'category': 'productivity-and-organization',
        'base_url': 'http://omi-app-hermes-agent:8000',
    },
]


def register_app(app: dict) -> None:
    # Imported here, not at module level: pulls in the backend's full
    # dependency set (firebase_admin, google-cloud-firestore, httpx), same
    # reasoning as seed_local_account.py's local_auth import.
    from database.apps import upsert_app_to_db
    from models.app import AppCreate
    from routers.apps import _process_chat_tools_manifest

    app_home_url = app['base_url']
    manifest_url = f"{app_home_url}/.well-known/omi-tools.json"

    data = {
        'id': app['slug'],
        'name': app['name'],
        'uid': None,
        'private': False,
        'approved': True,
        'status': 'approved',
        'category': app['category'],
        'author': 'Self-hosted',
        'description': app['description'],
        'image': '',
        'capabilities': {'chat'},
        'external_integration': {
            'chat_tools_manifest_url': manifest_url,
            'app_home_url': app_home_url,
        },
    }

    try:
        validated = AppCreate.model_validate(data)
    except Exception as e:
        print(f"SKIP {app['name']}: failed validation: {e}")
        return

    app_dict = validated.model_dump(exclude_unset=True)
    app_dict = _process_chat_tools_manifest(data['external_integration'], app_dict)

    tool_count = len(app_dict.get('chat_tools') or [])
    if tool_count == 0:
        print(
            f"WARNING {app['name']}: manifest fetch returned no tools "
            f"({manifest_url}) — is the container up and on the same network as omi-local?"
        )

    upsert_app_to_db(app_dict)
    print(f"Registered '{app['name']}' (id={app['slug']}, {tool_count} chat tools)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--slug', help='Firestore doc id for this app, e.g. local-myapp')
    parser.add_argument('--name', help='Display name shown in the app browser')
    parser.add_argument('--description', help='Shown in the app browser')
    parser.add_argument('--category', default='utilities', help='Default: utilities')
    parser.add_argument(
        '--mount', help='Gateway mount path, e.g. "myapp" for http://omi-apps-gateway:8080/myapp'
    )
    parser.add_argument(
        '--base-url', help='Full base URL instead of --mount, for an app not on the shared gateway'
    )
    parser.add_argument(
        '--skip',
        default='',
        help=(
            'Comma-separated substrings to exclude from the default bulk run '
            '(e.g. --skip hermes to leave out Hermes Agent until that container is actually up) — '
            'matched against each default app\'s slug, so a bare "hermes" is enough.'
        ),
    )
    args = parser.parse_args()

    single_app_args = [args.slug, args.name, args.description]
    if any(single_app_args) and not all(single_app_args):
        parser.error('--slug, --name, and --description must all be given together')
    if not any(single_app_args):
        skip_terms = [s.strip() for s in args.skip.split(',') if s.strip()]
        for app in DEFAULT_APPS:
            if any(term in app['slug'] for term in skip_terms):
                print(f"Skipping '{app['name']}' (matched --skip {args.skip!r})")
                continue
            register_app(app)
        return

    if bool(args.mount) == bool(args.base_url):
        parser.error('give exactly one of --mount or --base-url')
    base_url = args.base_url or f'{GATEWAY}/{args.mount}'

    register_app(
        {
            'slug': args.slug,
            'name': args.name,
            'description': args.description,
            'category': args.category,
            'base_url': base_url,
        }
    )


if __name__ == '__main__':
    main()
