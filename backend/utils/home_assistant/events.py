"""Omi -> Home Assistant events (omi-local): ``POST /api/events/omi_<type>``.

Opt-in per user and per type (database/home_assistant.py ``events``; OMI_HA_EVENTS is the
default for users who never chose). Types and what each carries (content-minimized: never
a transcript; ``event_detail: minimal`` also drops titles and overviews):

- ``omi_conversation_finished``: conversation id, title, a short overview, category,
  start/end, how many tasks it produced, person;
- ``omi_task_created``: one per task a finished conversation produced (at most
  MAX_TASK_EVENTS): the task text (short), due time, whether it waits for the user's OK;
- ``omi_task_due``: an approved, open task reached its due time (utils/home_assistant/waiting.py);
- ``omi_waiting_for_approval``: the number of things waiting for the user's OK went up
  (waiting.py), with the count per kind;
- ``omi_day_summary_ready``: the overnight day summary draft is ready to review: date, title.

Every event carries ``person`` (the name the user gave, so one automation can tell household
members apart) and ``source: omi``.

Fire-and-forget: callers already run off the request path (the post-processing pool, the
scheduler). ``fire`` never raises; a failure is one ``record_fallback`` (component webhook,
from home_assistant to none, outcome degraded) and the caller carries on. A Home Assistant
that keeps failing trips the shared webhook circuit breaker, so a dead HA costs nothing.

This module stays light: utils/webhooks.py imports it, and the pusher imports that.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from utils import task_approval
from utils.home_assistant.runner import db
from utils.home_assistant import client
from utils.home_assistant import settings as ha_settings
from utils.http_client import get_webhook_circuit_breaker
from utils.observability.fallback import record_fallback

logger = logging.getLogger(__name__)

EVENT_PREFIX = 'omi_'
EVENT_TIMEOUT_SECONDS = 3.0
MAX_TASK_EVENTS = 5
TITLE_CHARS = 120
OVERVIEW_CHARS = 280
TASK_CHARS = 120
_REASONS = {'unreachable': 'connection_error', 'blocked': 'policy', 'auth': 'auth_error', 'http': 'http_error'}


def short(value: Any, limit: int) -> str:
    text = ' '.join(str(value or '').split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + '…'


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if isinstance(value, datetime) else (str(value) if value else None)


async def settings_for(uid: str, settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if settings is not None:
        return settings
    loaded: Dict[str, Any] = await db(ha_settings.effective, uid)
    return loaded


async def fire(
    uid: str, event_type: str, payload: Dict[str, Any], *, settings: Optional[Dict[str, Any]] = None
) -> bool:
    """Fire ``omi_<event_type>`` when this user turned that type on. Never raises; True when
    Home Assistant took it."""
    try:
        settings = await settings_for(uid, settings)
        if not ha_settings.ready(settings) or not (settings.get('events') or {}).get(event_type):
            return False
        body = {**payload, 'person': settings.get('person') or '', 'source': 'omi'}
        breaker = get_webhook_circuit_breaker(f"{settings['url']}/api/events")
        if not breaker.allow_request():
            record_fallback(
                component='webhook',
                from_mode='home_assistant',
                to_mode='none',
                reason='circuit_open',
                outcome='degraded',
                log=logger,
            )
            return False
        try:
            await client.fire_event(settings, EVENT_PREFIX + event_type, body, timeout=EVENT_TIMEOUT_SECONDS)
        except client.HomeAssistantError as error:
            breaker.record_failure()
            record_fallback(
                component='webhook',
                from_mode='home_assistant',
                to_mode='none',
                reason=_REASONS.get(error.kind, 'other'),
                outcome='degraded',
                log=logger,
            )
            logger.warning('home_assistant: event %s not fired uid=%s kind=%s', event_type, uid, error.kind)
            return False
        breaker.record_success()
        return True
    except Exception as error:  # noqa: BLE001 — an event must never break its caller
        record_fallback(
            component='webhook',
            from_mode='home_assistant',
            to_mode='none',
            reason='other',
            outcome='degraded',
            log=logger,
        )
        logger.warning('home_assistant: event %s failed uid=%s %s', event_type, uid, type(error).__name__)
        return False


def conversation_payload(conversation: Any, *, minimal: bool) -> Dict[str, Any]:
    structured = getattr(conversation, 'structured', None)
    items = [a for a in (getattr(structured, 'action_items', None) or []) if getattr(a, 'description', '')]
    payload: Dict[str, Any] = {
        'conversation_id': getattr(conversation, 'id', ''),
        'category': str(
            getattr(getattr(structured, 'category', None), 'value', '') or getattr(structured, 'category', '') or ''
        ),
        'started_at': _iso(getattr(conversation, 'started_at', None) or getattr(conversation, 'created_at', None)),
        'finished_at': _iso(getattr(conversation, 'finished_at', None)),
        'task_count': len(items),
    }
    if not minimal:
        payload['title'] = short(getattr(structured, 'title', ''), TITLE_CHARS)
        payload['overview'] = short(getattr(structured, 'overview', ''), OVERVIEW_CHARS)
    return payload


def task_payload(item: Any, conversation_id: str, *, minimal: bool) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        'conversation_id': conversation_id,
        'due_at': _iso(getattr(item, 'due_at', None)),
        # Extracted tasks wait for the user's OK unless the operator turned that off.
        'waiting_for_approval': task_approval.approval_enabled(),
    }
    if not minimal:
        payload['description'] = short(getattr(item, 'description', ''), TASK_CHARS)
    return payload


async def conversation_finished(uid: str, conversation: Any) -> None:
    """After a conversation is processed (utils/webhooks.conversation_created_webhook): the
    conversation, then each task it produced. Never raises."""
    try:
        if getattr(conversation, 'is_locked', False) or getattr(conversation, 'discarded', False):
            return
        settings = await db(ha_settings.effective, uid)
        wanted = settings.get('events') or {}
        if not ha_settings.ready(settings) or not (wanted.get('conversation_finished') or wanted.get('task_created')):
            return
        minimal = settings.get('event_detail') == 'minimal'
        await fire(uid, 'conversation_finished', conversation_payload(conversation, minimal=minimal), settings=settings)
        structured = getattr(conversation, 'structured', None)
        items = [a for a in (getattr(structured, 'action_items', None) or []) if getattr(a, 'description', '')]
        for item in items[:MAX_TASK_EVENTS]:
            payload = task_payload(item, getattr(conversation, 'id', ''), minimal=minimal)
            await fire(uid, 'task_created', payload, settings=settings)
    except Exception as error:  # noqa: BLE001 — conversation processing never waits on Home Assistant
        record_fallback(
            component='webhook', from_mode='home_assistant', to_mode='none', reason='other', outcome='degraded'
        )
        logger.warning('home_assistant: conversation event failed uid=%s %s', uid, type(error).__name__)


async def day_summary_ready(uid: str, date_str: str, title: str = '') -> None:
    """The overnight day summary draft is ready (utils/day_bundle_scheduler.py). Never raises."""
    try:
        settings = await db(ha_settings.effective, uid)
    except Exception as error:  # noqa: BLE001 — the draft stands either way
        record_fallback(
            component='webhook', from_mode='home_assistant', to_mode='none', reason='unavailable', outcome='degraded'
        )
        logger.warning('home_assistant: day event settings unreadable uid=%s %s', uid, type(error).__name__)
        return
    payload: Dict[str, Any] = {'date': date_str}
    if settings.get('event_detail') != 'minimal':
        payload['title'] = short(title, TITLE_CHARS)
    await fire(uid, 'day_summary_ready', payload, settings=settings)
