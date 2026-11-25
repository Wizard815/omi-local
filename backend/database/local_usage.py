"""Local usage data (omi-local): per-day event counts and recent events.

Two small documents, no queries and no indexes:

- ``users/{uid}/local_usage/summary``: that person's counts per UTC day
  (``days: {day: {event: n}}``) and their newest events (``recent``), each
  with a few closed, already-sanitized properties.
- ``local_usage/summary``: the server's counts per day and how many events
  each person produced per day (``people: {day: {uid: n}}``), for the admin.

Every write prunes days older than the retention window and caps ``recent``,
so neither document grows past the window. Config: config/local_usage.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

from google.cloud import firestore

from config.local_usage import RECENT_EVENTS_KEPT
from database._client import get_firestore_client, run_transactional

COLLECTION = 'local_usage'
SUMMARY_DOC = 'summary'
_MAX_PROPERTIES = 12
_MAX_STRING = 80
# Same on every event of this server; they only add noise to a local view.
_DROPPED_PROPERTIES = frozenset({'emitter', 'environment'})


def utc_day(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime('%Y-%m-%d')


def _oldest_kept_day(today: str, retention_days: int) -> str:
    start = datetime.strptime(today, '%Y-%m-%d') - timedelta(days=max(retention_days, 1) - 1)
    return start.strftime('%Y-%m-%d')


def _kept_days(days: Any, today: str, retention_days: int) -> dict[str, dict[str, int]]:
    oldest = _oldest_kept_day(today, retention_days)
    if not isinstance(days, Mapping):
        return {}
    return {
        day: {str(key): int(value) for key, value in counts.items() if isinstance(value, int)}
        for day, counts in days.items()
        if isinstance(day, str) and oldest <= day <= today and isinstance(counts, Mapping)
    }


def _prune_anchor(data: Mapping[str, Any], day: str) -> str:
    """Prune against the newest day seen, so a late, back-dated event cannot drop newer days."""
    days = data.get('days')
    newest = max((d for d in days if isinstance(d, str)), default=day) if isinstance(days, Mapping) else day
    return max(newest, day)


def stored_properties(properties: Mapping[Any, Any]) -> dict[str, Any]:
    """Keep only short scalar values; the caller has already dropped ids and content."""
    kept: dict[str, Any] = {}
    for key, value in properties.items():
        if len(kept) >= _MAX_PROPERTIES:
            break
        if not isinstance(key, str) or key.startswith('$') or key in _DROPPED_PROPERTIES:
            continue
        if isinstance(value, bool) or isinstance(value, (int, float)):
            kept[key] = value
        elif isinstance(value, str):
            kept[key] = value[:_MAX_STRING]
    return kept


def apply_user_event(
    data: Mapping[str, Any],
    *,
    event: str,
    at: datetime,
    properties: Mapping[str, Any],
    retention_days: int,
) -> dict[str, Any]:
    today = utc_day(at)
    anchor = _prune_anchor(data, today)
    days = _kept_days(data.get('days'), anchor, retention_days)
    counts = days.setdefault(today, {})
    counts[event] = counts.get(event, 0) + 1
    recent = [item for item in (data.get('recent') or []) if isinstance(item, Mapping)]
    oldest = _oldest_kept_day(anchor, retention_days)
    recent = [item for item in recent if str(item.get('at', ''))[:10] >= oldest]
    recent.insert(0, {'event': event, 'at': at.astimezone(timezone.utc).isoformat(), 'properties': dict(properties)})
    recent.sort(key=lambda item: str(item.get('at', '')), reverse=True)
    return {'days': days, 'recent': recent[:RECENT_EVENTS_KEPT], 'updated_at': at}


def apply_server_event(
    data: Mapping[str, Any], *, uid: str, event: str, at: datetime, retention_days: int
) -> dict[str, Any]:
    today = utc_day(at)
    anchor = _prune_anchor(data, today)
    days = _kept_days(data.get('days'), anchor, retention_days)
    counts = days.setdefault(today, {})
    counts[event] = counts.get(event, 0) + 1
    people = _kept_days(data.get('people'), anchor, retention_days)
    per_person = people.setdefault(today, {})
    per_person[uid] = per_person.get(uid, 0) + 1
    return {'days': days, 'people': people, 'updated_at': at}


def _user_ref(client: Any, uid: str) -> Any:
    return client.collection('users').document(uid).collection(COLLECTION).document(SUMMARY_DOC)


def _server_ref(client: Any) -> Any:
    return client.collection(COLLECTION).document(SUMMARY_DOC)


@firestore.transactional
def _rewrite(transaction: Any, ref: Any, update: Any) -> None:
    snapshot = ref.get(transaction=transaction)
    data = (snapshot.to_dict() or {}) if snapshot.exists else {}
    transaction.set(ref, update(data))


def record_event(
    uid: str,
    event: str,
    properties: Mapping[str, Any],
    *,
    at: datetime,
    retention_days: int,
    firestore_client: Optional[Any] = None,
) -> None:
    client = firestore_client if firestore_client is not None else get_firestore_client()
    kept = stored_properties(properties)
    run_transactional(
        client,
        _rewrite,
        _user_ref(client, uid),
        lambda data: apply_user_event(data, event=event, at=at, properties=kept, retention_days=retention_days),
    )
    run_transactional(
        client,
        _rewrite,
        _server_ref(client),
        lambda data: apply_server_event(data, uid=uid, event=event, at=at, retention_days=retention_days),
    )


def get_user_summary(uid: str, *, firestore_client: Optional[Any] = None) -> dict[str, Any]:
    client = firestore_client if firestore_client is not None else get_firestore_client()
    snapshot = _user_ref(client, uid).get()
    return (snapshot.to_dict() or {}) if snapshot.exists else {}


def get_server_summary(*, firestore_client: Optional[Any] = None) -> dict[str, Any]:
    client = firestore_client if firestore_client is not None else get_firestore_client()
    snapshot = _server_ref(client).get()
    return (snapshot.to_dict() or {}) if snapshot.exists else {}


def _window(days: Any, today: str, window_days: int) -> list[dict[str, Any]]:
    kept = _kept_days(days, today, window_days)
    return [{'day': day, 'counts': kept[day], 'total': sum(kept[day].values())} for day in sorted(kept, reverse=True)]


def _totals(rows: list[dict[str, Any]], field: str = 'counts') -> dict[str, int]:
    totals: dict[str, int] = {}
    for row in rows:
        for key, value in row[field].items():
            totals[key] = totals.get(key, 0) + value
    return dict(sorted(totals.items(), key=lambda item: (-item[1], item[0])))


def user_view(data: Mapping[str, Any], *, now: datetime, window_days: int) -> dict[str, Any]:
    """One person's own counts and recent events, newest first."""
    today = utc_day(now)
    days = _window(data.get('days'), today, window_days)
    oldest = _oldest_kept_day(today, window_days)
    recent = [
        item
        for item in (data.get('recent') or [])
        if isinstance(item, Mapping) and str(item.get('at', ''))[:10] >= oldest
    ]
    return {'window_days': window_days, 'totals': _totals(days), 'days': days, 'recent': recent}


def server_view(data: Mapping[str, Any], *, now: datetime, window_days: int) -> dict[str, Any]:
    """All people, aggregate: counts per event and day, active people per day."""
    today = utc_day(now)
    days = _window(data.get('days'), today, window_days)
    people_rows = [
        {'day': day, 'counts': counts} for day, counts in _kept_days(data.get('people'), today, window_days).items()
    ]
    active = {row['day']: len(row['counts']) for row in people_rows}
    for row in days:
        row['active_people'] = active.get(row['day'], 0)
    return {
        'window_days': window_days,
        'totals': _totals(days),
        'events_per_person': _totals(people_rows),
        'days': days,
    }
