"""Single-container gateway for the self-hosted no-auth chat-tool apps.

Mounts each app's own FastAPI instance under its own path prefix instead of
running one container per app. These apps are small, stateless, read-only
wrappers around a public API with no shared state and no route
collisions, so one process serving all of them uses far less memory/CPU
than N containers, needs zero new Docker Compose services or network
wiring per app, and a rebuild picks up every app at once.

Adding another app: copy its main.py into the Dockerfile (one COPY line),
import and mount it below, done — no new compose service, no new network
attachment to babysit.

Each mounted app's own relative endpoint paths (as declared in its
/.well-known/omi-tools.json, e.g. "/tools/search_articles") resolve
correctly under its mount prefix because FastAPI's app.mount() strips the
prefix before routing into the sub-app — the sub-app never needs to know
it isn't running standalone. backend/scripts/register_chat_apps.py sets
each app's app_home_url to its mount path (e.g. ".../wikipedia") so the
backend's own manifest-fetch resolves those relative endpoints against the
right prefix.
"""

from fastapi import FastAPI

from apps.wikipedia.main import app as wikipedia_app
from apps.open_library.main import app as open_library_app
from apps.open_meteo.main import app as open_meteo_app
from apps.openfoodfacts.main import app as openfoodfacts_app

app = FastAPI(title="omi-local chat-tool apps gateway")

app.mount("/wikipedia", wikipedia_app)
app.mount("/open-library", open_library_app)
app.mount("/open-meteo", open_meteo_app)
app.mount("/openfoodfacts", openfoodfacts_app)

MOUNTED_APPS = ["wikipedia", "open-library", "open-meteo", "openfoodfacts"]


@app.get("/health")
def health():
    return {"status": "ok", "apps": MOUNTED_APPS}
