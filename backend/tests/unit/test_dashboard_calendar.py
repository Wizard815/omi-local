"""The self-hosting dashboard shows the ALLGlance / calendar wiring: the gateway and CalDAV as
components, and one row per member (adds events as, events to check, last CalDAV sync)."""

import asyncio
import importlib
from datetime import datetime, timezone

import pytest


@pytest.fixture
def dashboard(monkeypatch):
    module = importlib.import_module("routers.dashboard")
    for key in (
        "OPENAI_BASE_URL",
        "LOCAL_VECTOR_DB",
        "SEARXNG_URL",
        "ALLGLANCE_URL",
        "ALLGLANCE_TOKEN",
        "CALDAV_DEFAULT_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOSTED_PARAKEET_API_URL", "")
    return module


def _stub_http(module, monkeypatch, *, health=None, caldav_ok=True):
    probed = []

    async def _check_http(url, timeout=5.0):
        probed.append(url)
        return (True, 401, "") if caldav_ok else (False, None, "refused")

    async def _gateway_health(url, timeout=3.0):
        probed.append(f"{url}/health")
        return health

    monkeypatch.setattr(module, "_check_http", _check_http)
    monkeypatch.setattr(module, "_gateway_health", _gateway_health)
    return probed


def test_unwired_stack_is_not_configured_and_not_probed(dashboard, monkeypatch):
    probed = _stub_http(dashboard, monkeypatch)
    components = asyncio.run(dashboard._get_async_components())
    assert components["allglance_gateway"]["status"] == "not_configured"
    assert components["caldav_server"]["status"] == "not_configured"
    assert probed == []
    overview = asyncio.run(dashboard._calendar_overview())
    assert overview["gateway_url"] == "" and overview["members"] == []
    assert "Not wired" in dashboard._render_calendar_card(overview)


def test_gateway_health_names_the_adapters_and_caldav_answers_with_a_login_prompt(dashboard, monkeypatch):
    monkeypatch.setenv("ALLGLANCE_URL", "http://nas:8090/")
    monkeypatch.setenv("CALDAV_DEFAULT_URL", "http://nas:8090/dav/")
    probed = _stub_http(
        dashboard,
        monkeypatch,
        health={"adapters": {"vikunja": {"ok": True}, "dayglance": {"ok": False}, "omi": {"ok": True}}},
    )
    components = asyncio.run(dashboard._get_async_components())
    gateway = components["allglance_gateway"]
    assert (
        gateway["status"] == "up" and "up: omi, vikunja" in gateway["detail"] and "down: dayglance" in gateway["detail"]
    )
    assert components["caldav_server"] == {"status": "up", "detail": "http://nas:8090/dav/ (401)"}
    assert "http://nas:8090/health" in probed and "http://nas:8090/dav/" in probed


def test_members_rows_show_binding_pending_count_and_last_sync(dashboard, monkeypatch):
    monkeypatch.setenv("ALLGLANCE_URL", "http://nas:8090")
    monkeypatch.setenv("ALLGLANCE_TOKEN", "gw")
    _stub_http(dashboard, monkeypatch, health={"adapters": {"vikunja": {"ok": True}, "dayglance": {"ok": True}}})
    import database.caldav_settings as caldav_settings_db
    import database.day_bundle_users as active_users_db
    import database.users as users_db
    from utils import family_calendar

    monkeypatch.setattr(active_users_db, "active_users", lambda: ["u-a", "u-b"])
    monkeypatch.setattr(users_db, "get_user_profile", lambda uid: {"display_name": "Ada"} if uid == "u-a" else {})
    monkeypatch.setattr(
        family_calendar,
        "integration",
        lambda uid: (
            {"base_url": "http://nas:8090", "access_token": "gw", "owner_id": "u-sam"} if uid == "u-a" else None
        ),
    )
    monkeypatch.setattr(family_calendar, "pending", lambda uid: ([{"title": "Inspection"}, {"title": "Dinner"}], []))
    synced = datetime(2026, 11, 7, 9, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(
        caldav_settings_db,
        "get_settings",
        lambda uid: {"last_sync_at": synced, "last_error": ""} if uid == "u-a" else None,
    )

    async def members(target, client=None):
        return [{"id": "u-sam", "name": "Sam"}, {"id": "u-dana", "name": "Dana"}]

    monkeypatch.setattr(family_calendar, "members", members)
    overview = asyncio.run(dashboard._calendar_overview())
    assert overview["dayglance"] is True and overview["vikunja"] is True and overview["token_set"]
    ada, other = overview["members"]
    assert ada["name"] == "Ada" and ada["owner_name"] == "Sam" and ada["pending"] == 2 and ada["last_sync_at"] == synced
    assert other["configured"] is False and other["pending"] == 0
    html = dashboard._render_calendar_card(overview)
    assert "Ada" in html and "Sam" in html and "2026-11-07 09:30" in html and "not wired" in html
    assert "dayGLANCE (events)</td><td>✅ on" in html
