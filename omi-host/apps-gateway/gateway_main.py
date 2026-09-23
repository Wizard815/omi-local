"""Single-container gateway for the self-hosted no-auth chat-tool apps.

Reads OMI_APPS_LIST (comma-separated slugs, e.g. "wikipedia,open-library")
and dynamically imports + mounts plugins/omi-<slug>-app/main.py for each —
at container START, not image BUILD. plugins/ is bind-mounted read-only
(see docker-compose.yml), not baked in with COPY, so adding an app whose
source already exists in this repo is: add its slug to OMI_APPS_LIST in
.env, then `docker compose restart omi-apps-gateway` — no image rebuild.
A genuinely new Python dependency not already in requirements.txt still
needs one, but that's the exception, not the routine case of enabling one
more of these small no-auth apps.

Each mounted app's own relative endpoint paths (as declared in its
/.well-known/omi-tools.json, e.g. "/tools/search_articles") resolve
correctly under its mount prefix because FastAPI's app.mount() strips the
prefix before routing into the sub-app — the sub-app never needs to know
it isn't running standalone. backend/scripts/register_chat_apps.py sets
each app's app_home_url to its mount path so the backend's own
manifest-fetch resolves those relative endpoints against the right prefix.
"""

import importlib.util
import logging
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Optional

from fastapi import FastAPI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('omi-apps-gateway')

PLUGINS_DIR = Path(os.getenv('OMI_APPS_PLUGINS_DIR', '/app/plugins'))
REQUESTED = [slug.strip() for slug in os.getenv('OMI_APPS_LIST', '').split(',') if slug.strip()]

app = FastAPI(title='omi-local chat-tool apps gateway')
mounted: list[str] = []


def _load_app_module(slug: str) -> Optional[ModuleType]:
    main_path = PLUGINS_DIR / f'omi-{slug}-app' / 'main.py'
    if not main_path.is_file():
        logger.error("OMI_APPS_LIST includes %r but %s doesn't exist — skipping", slug, main_path)
        return None

    # Each app's main.py is `import main`-shaped, not a real package, and
    # several share the literal filename "main.py" — load each under its
    # own synthetic module name so they don't shadow each other in
    # sys.modules.
    module_name = f'_omi_app_{slug.replace("-", "_")}'
    spec = importlib.util.spec_from_file_location(module_name, main_path)
    if spec is None or spec.loader is None:
        logger.error('Could not build an import spec for %s — skipping', main_path)
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        logger.exception('Failed to import %s', main_path)
        del sys.modules[module_name]
        return None
    return module


for _slug in REQUESTED:
    _module = _load_app_module(_slug)
    if _module is None:
        continue
    _sub_app = getattr(_module, 'app', None)
    if _sub_app is None:
        logger.error('%s/main.py has no top-level `app` FastAPI instance — skipping', _slug)
        continue
    app.mount(f'/{_slug}', _sub_app)
    mounted.append(_slug)
    logger.info('Mounted /%s from plugins/omi-%s-app', _slug, _slug)

if not mounted:
    logger.warning('No apps mounted — check OMI_APPS_LIST and that plugins/ is bind-mounted correctly')


@app.get('/health')
def health():
    return {'status': 'ok', 'apps': mounted, 'requested': REQUESTED}
