#!/usr/bin/env python3
"""Register the self-hosted chat-tool apps (omi-host/docker-compose.yml's
OMI_APPS_ENABLED services) in this deployment's local Firestore, so they
show up in the app browser without going through Omi's real cloud approval
pipeline.

Bypasses POST /v1/apps intentionally rather than calling it: that endpoint
requires a multipart image upload and rejects an app with neither
external_integration.triggers_on nor actions (both correct for a real
webhook/action app, both wrong for a pure chat-tools app like these). This
script builds the same AppCreate shape that endpoint validates against and
reuses its actual manifest-fetch logic (routers.apps._process_chat_tools_manifest)
instead of reimplementing it, so tool definitions are populated the same way
a real app creation would.

Run inside the backend container, same as seed_local_account.py:

    docker exec -it omi-local python backend/scripts/register_chat_apps.py

Idempotent: re-running overwrites each app's document in place (upsert_app_to_db
uses Firestore .set(), not .add()), so it doubles as a "re-fetch manifests"
tool after you change/redeploy one of these services.
"""

import os

# See seed_local_account.py for why these need setdefault() rather than a
# plain os.environ[...] — entrypoint.sh exports them for the backend process
# it launches, but a fresh `docker exec` session doesn't inherit those.
os.environ.setdefault('FIRESTORE_EMULATOR_HOST', '127.0.0.1:8085')
os.environ.setdefault('FIREBASE_AUTH_EMULATOR_HOST', '127.0.0.1:9099')
os.environ.setdefault('FIREBASE_AUTH_PROJECT_ID', 'demo-omi-local')
os.environ.setdefault('FIREBASE_PROJECT_ID', 'demo-omi-local')
os.environ.setdefault('FIRESTORE_DATABASE_ID', 'default')

# base_url must be reachable from the omi-local container. The four
# gateway-mounted apps share one container (omi-apps-gateway, see
# omi-host/apps-gateway/) and are distinguished only by mount path; Hermes
# Agent is its own separate container. Either way, this relies on Compose's
# per-network DNS (same container name as the compose service), so these
# only resolve once omi-local and these app services share a network (see
# docker-compose.override.yml.example's omi-apps section — a service not on
# the same network can't be reached by name even though it's running).
_GATEWAY = 'http://omi-apps-gateway:8080'

APPS = [
    {
        'slug': 'local-wikipedia',
        'name': 'Wikipedia',
        'description': 'Search Wikipedia articles and fetch summaries during a conversation.',
        'category': 'utilities',
        'base_url': f'{_GATEWAY}/wikipedia',
    },
    {
        'slug': 'local-open-library',
        'name': 'Open Library',
        'description': 'Search books, fetch metadata, and browse subject recommendations.',
        'category': 'utilities',
        'base_url': f'{_GATEWAY}/open-library',
    },
    {
        'slug': 'local-open-meteo',
        'name': 'Weather (Open-Meteo)',
        'description': 'Current weather, short forecasts, and air-quality readings.',
        'category': 'utilities',
        'base_url': f'{_GATEWAY}/open-meteo',
    },
    {
        'slug': 'local-openfoodfacts',
        'name': 'Open Food Facts',
        'description': 'Look up packaged food nutrition, ingredients, and allergens by name or barcode.',
        'category': 'health-and-fitness',
        'base_url': f'{_GATEWAY}/openfoodfacts',
    },
    {
        'slug': 'local-hermes-agent',
        'name': 'Hermes Agent',
        'description': 'Ask Hermes Agent (self-hosted, on a separate machine) a question from Omi chat.',
        'category': 'productivity-and-organization',
        'base_url': 'http://omi-app-hermes-agent:8000',
    },
]


def main() -> None:
    # Imported here, not at module level: pulls in the backend's full
    # dependency set (firebase_admin, google-cloud-firestore, httpx), same
    # reasoning as seed_local_account.py's local_auth import.
    from database.apps import upsert_app_to_db
    from models.app import AppCreate
    from routers.apps import _process_chat_tools_manifest

    for app in APPS:
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
            continue

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


if __name__ == '__main__':
    main()
