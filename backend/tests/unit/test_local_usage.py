"""Local usage store (omi-local): counts per day, recent events, retention."""

from datetime import datetime, timedelta, timezone

from config.local_usage import RECENT_EVENTS_KEPT, retention_days, usage_enabled
from routers import dashboard, users
from database import local_usage
from tests.unit.fixtures.strict_firestore_transaction import StrictFirestore

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


def test_switch_defaults_to_self_hosted_and_can_be_turned_off():
    assert usage_enabled({}) is False
    assert usage_enabled({'OMI_SELF_HOSTED': '1'}) is True
    assert usage_enabled({'OMI_SELF_HOSTED': '1', 'OMI_LOCAL_USAGE': 'off'}) is False
    assert usage_enabled({'OMI_LOCAL_USAGE': 'on'}) is True


def test_retention_days_is_bounded():
    assert retention_days({}) == 90
    assert retention_days({'OMI_LOCAL_USAGE_RETENTION_DAYS': '7'}) == 7
    assert retention_days({'OMI_LOCAL_USAGE_RETENTION_DAYS': '0'}) == 1
    assert retention_days({'OMI_LOCAL_USAGE_RETENTION_DAYS': '9999'}) == 365
    assert retention_days({'OMI_LOCAL_USAGE_RETENTION_DAYS': 'soon'}) == 90


def test_stored_properties_keep_only_short_scalars():
    kept = local_usage.stored_properties(
        {
            'status': 'x' * 200,
            'count': 3,
            'ok': True,
            'nested': {'a': 1},
            'items': [1],
            '$insert_id': 'i',
            'emitter': 'server',
            'environment': 'local',
        }
    )
    assert kept == {'status': 'x' * 80, 'count': 3, 'ok': True}


def test_user_event_counts_prunes_old_days_and_caps_recent():
    data = {
        'days': {'2026-01-01': {'Old': 5}, '2026-11-24': {'A': 1}},
        'recent': [{'event': 'Old', 'at': '2026-01-01T00:00:00+00:00', 'properties': {}}],
    }
    for _ in range(RECENT_EVENTS_KEPT + 5):
        data = local_usage.apply_user_event(data, event='A', at=NOW, properties={'k': 1}, retention_days=30)
    assert data['days'] == {'2026-11-24': {'A': 1}, '2026-11-25': {'A': RECENT_EVENTS_KEPT + 5}}
    assert len(data['recent']) == RECENT_EVENTS_KEPT
    assert all(item['event'] == 'A' for item in data['recent'])


def test_server_event_counts_people_per_day():
    data = {}
    for uid in ('a', 'a', 'b'):
        data = local_usage.apply_server_event(data, uid=uid, event='E', at=NOW, retention_days=30)
    old = NOW - timedelta(days=40)
    data['days'][local_usage.utc_day(old)] = {'E': 9}
    data = local_usage.apply_server_event(data, uid='c', event='F', at=NOW, retention_days=30)
    assert data['days'] == {'2026-11-25': {'E': 3, 'F': 1}}
    assert data['people'] == {'2026-11-25': {'a': 2, 'b': 1, 'c': 1}}


def test_record_event_writes_both_documents_in_transactions():
    fake = StrictFirestore()
    local_usage.record_event(
        'u1', 'E', {'count': 2, 'emitter': 'server'}, at=NOW, retention_days=30, firestore_client=fake
    )
    local_usage.record_event('u2', 'E', {}, at=NOW, retention_days=30, firestore_client=fake)

    mine = local_usage.get_user_summary('u1', firestore_client=fake)
    assert mine['days'] == {'2026-11-25': {'E': 1}}
    assert mine['recent'][0]['properties'] == {'count': 2}
    server = local_usage.get_server_summary(firestore_client=fake)
    assert server['days'] == {'2026-11-25': {'E': 2}}
    assert server['people'] == {'2026-11-25': {'u1': 1, 'u2': 1}}


def test_views_are_windowed_and_sorted():
    data = {}
    for day_offset, event in ((0, 'A'), (0, 'A'), (1, 'B'), (20, 'C')):
        at = NOW - timedelta(days=day_offset)
        data = local_usage.apply_user_event(data, event=event, at=at, properties={}, retention_days=90)
    view = local_usage.user_view(data, now=NOW, window_days=7)
    assert view['totals'] == {'A': 2, 'B': 1}
    assert [row['day'] for row in view['days']] == ['2026-11-25', '2026-11-24']
    assert {item['event'] for item in view['recent']} == {'A', 'B'}

    server = {}
    for uid, event in (('a', 'A'), ('b', 'A'), ('a', 'B')):
        server = local_usage.apply_server_event(server, uid=uid, event=event, at=NOW, retention_days=90)
    view = local_usage.server_view(server, now=NOW, window_days=7)
    assert view['totals'] == {'A': 2, 'B': 1}
    assert view['events_per_person'] == {'a': 2, 'b': 1}
    assert view['days'][0]['active_people'] == 2


def test_back_dated_event_does_not_drop_newer_days():
    data = local_usage.apply_user_event({}, event='New', at=NOW, properties={}, retention_days=30)
    data = local_usage.apply_user_event(
        data, event='Late', at=NOW - timedelta(days=2), properties={}, retention_days=30
    )
    assert data['days'] == {'2026-11-25': {'New': 1}, '2026-11-23': {'Late': 1}}
    assert [item['event'] for item in data['recent']] == ['New', 'Late']


def test_person_sees_only_their_own_and_admin_sees_the_aggregate(monkeypatch):

    fake = StrictFirestore()
    local_usage.record_event('me', 'Chat', {}, at=datetime.now(timezone.utc), retention_days=90, firestore_client=fake)
    local_usage.record_event(
        'other', 'Sync', {}, at=datetime.now(timezone.utc), retention_days=90, firestore_client=fake
    )
    monkeypatch.setattr(local_usage, 'get_firestore_client', lambda: fake)
    monkeypatch.setenv('OMI_SELF_HOSTED', '1')
    monkeypatch.setenv('OMI_LOCAL_USAGE_RETENTION_DAYS', '14')

    mine = users.get_my_local_usage(days=30, uid='me')
    assert mine['enabled'] is True and mine['retention_days'] == 14 and mine['window_days'] == 14
    assert mine['totals'] == {'Chat': 1}
    assert [item['event'] for item in mine['recent']] == ['Chat']

    server = dashboard.dashboard_usage(days=30)
    assert server['totals'] == {'Chat': 1, 'Sync': 1}
    assert server['events_per_person'] == {'me': 1, 'other': 1}

    monkeypatch.setenv('OMI_LOCAL_USAGE', 'off')
    assert users.get_my_local_usage(days=30, uid='me')['totals'] == {}
    assert dashboard.dashboard_usage(days=30)['enabled'] is False
