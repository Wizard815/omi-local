"""utils/caldav/sync.py: what moves in which direction, and only when something changed."""

from datetime import datetime, timedelta, timezone

import pytest

import database.caldav_settings as settings_db
from utils.caldav import ical
from utils.caldav import sync
from utils.caldav.client import CalDavError, Calendar

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
HOME = 'http://dav.test/alice/'


class _FakeClient:
    """Records PUTs; serves whatever the test put into ``objects`` on REPORT."""

    instances = []

    def __init__(self, url, username, password):
        self.url, self.username, self.password = url, username, password
        self.puts = []
        self.made = []
        self.objects = {}  # href -> ics
        self.fail_put = set()
        _FakeClient.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def calendar_home(self):
        return HOME

    def list_calendars(self, home=None):
        return [Calendar(href=HOME + 'personal/', name='Personal', components=['VEVENT'])]

    def make_calendar(self, href, name, components):
        self.made.append((href, name, list(components)))

    def put(self, href, ics):
        if href in self.fail_put:
            raise CalDavError('PUT: HTTP 507', 507)
        self.puts.append(href)
        self.objects[href] = ics
        return '"etag"'

    def query(self, href, component, start=None, end=None):
        from utils.caldav.client import CalendarObject

        return [
            CalendarObject(href=h, etag='"e"', data=ics)
            for h, ics in self.objects.items()
            if h.startswith(href) and f'BEGIN:{component}' in ics
        ]


class _Redis:
    def __init__(self):
        self.h = {}

    def hget(self, key, field):
        return self.h.get(key, {}).get(field)

    def hset(self, key, field, value):
        self.h.setdefault(key, {})[field] = value

    def hdel(self, key, field):
        self.h.get(key, {}).pop(field, None)


@pytest.fixture
def world(monkeypatch):
    _FakeClient.instances.clear()
    tasks = {
        't1': {
            'id': 't1',
            'description': 'Book the retro room',
            'completed': False,
            'due_at': NOW + timedelta(days=2),
            'conversation_id': 'c1',
            'updated_at': NOW,
        },
        't2': {
            'id': 't2',
            'description': 'Old done thing',
            'completed': True,
            'completed_at': NOW - timedelta(days=60),
        },
        't3': {'id': 't3', 'description': 'Recently done', 'completed': True, 'completed_at': NOW - timedelta(days=1)},
    }
    created = []
    reviews = [
        {
            'date': '2026-10-26',
            'title': 'Coffee and calls',
            'summary_text': 'You had coffee.',
            'corrections': [],
            'sections': [{'tags': ['coffee'], 'conversation_ids': ['c1']}],
            'kept_conversation_ids': [],
            'rolled_up_conversation_ids': ['c1'],
        },
    ]
    conversations = [
        {
            'id': 'c1',
            'status': 'completed',
            'started_at': NOW - timedelta(hours=3),
            'finished_at': NOW - timedelta(hours=2),
            'tags': ['work'],
            'structured': {
                'title': 'Planning',
                'emoji': '📝',
                'overview': 'We planned.',
                'events': [{'title': 'Retro', 'start': (NOW + timedelta(days=2)).isoformat(), 'duration': 45}],
            },
        },
    ]
    stored = {}
    redis = _Redis()
    monkeypatch.setattr(sync.redis_db, 'r', redis)
    monkeypatch.setattr(
        sync.action_items_db, 'get_action_items', lambda uid, limit=None, **k: [dict(t) for t in tasks.values()]
    )
    monkeypatch.setattr(sync.action_items_db, 'get_action_item', lambda uid, tid: tasks.get(tid))
    monkeypatch.setattr(
        sync.action_items_db,
        'mark_action_item_completed',
        lambda uid, tid, done=True: tasks[tid].update(completed=done) or True,
    )

    def create(uid, data, idempotency_key=None, **k):
        tid = f'new{len(created) + 1}'
        tasks[tid] = {'id': tid, **data}
        created.append((tid, data, idempotency_key))
        return tid

    monkeypatch.setattr(sync.action_items_db, 'create_action_item', create)
    monkeypatch.setattr(sync.day_reviews_db, 'list_day_reviews', lambda uid, limit=30: list(reviews))
    monkeypatch.setattr(
        sync.conversations_db,
        'get_conversations_by_id_without_photos',
        lambda uid, ids, include_discarded=False: [dict(c) for c in conversations if c['id'] in ids],
    )
    monkeypatch.setattr(
        sync.device_calendar_db, 'store_events', lambda uid, events, source='device': stored.__setitem__(source, events)
    )
    settings = {**settings_db.DEFAULTS, 'url': 'http://dav.test/', 'username': 'alice', 'password': 'pw'}
    return settings, tasks, created, redis, stored


def _run(settings):
    return sync.sync_user('u1', settings, client_factory=_FakeClient, now=NOW)


def test_first_sync_creates_calendars_and_pushes_tasks_summary_and_events(world):
    settings, tasks, created, redis, stored = world
    report = _run(settings)
    client = _FakeClient.instances[-1]
    # the bundled server gets an "Omi Tasks" calendar; events go to the existing one
    assert client.made == [(HOME + 'omi-tasks/', 'Omi Tasks', ['VTODO'])]
    assert report.tasks_pushed == 2  # t1 and the recently completed t3; t2 is older than the window
    assert report.events_pushed == 2  # the day summary and the extracted event; no conversation blocks by default
    assert report.errors == [] and report.tasks_pulled == 0 and report.tasks_imported == 0
    assert HOME + 'omi-tasks/omi-task-t1@omi-local.ics' in client.puts
    task = client.objects[HOME + 'omi-tasks/omi-task-t1@omi-local.ics']
    assert 'SUMMARY:Book the retro room' in task and 'STATUS:NEEDS-ACTION' in task and 'DUE:20261029T120000Z' in task
    day = client.objects[HOME + 'personal/omi-day-2026-10-26@omi-local.ics']
    assert (
        'DTSTART;VALUE=DATE:20261026' in day
        and 'SUMMARY:Omi: Coffee and calls' in day
        and 'CATEGORIES:omi,coffee' in day
    )
    event = client.objects[HOME + 'personal/omi-event-c1-0@omi-local.ics']
    assert 'SUMMARY:Retro' in event and 'DTEND:20261029T124500Z' in event and 'CATEGORIES:omi,work' in event
    # our own pushes are not reported back as "the user's calendar"
    assert stored['caldav'] == [] and report.events_pulled == 0


def test_second_sync_puts_nothing_when_nothing_changed(world):
    settings, tasks, created, redis, stored = world
    _run(settings)
    first = _FakeClient.instances[-1]
    report = _run(settings)
    second = _FakeClient.instances[-1]
    assert second is not first and second.puts == [] and report.tasks_pushed == 0 and report.events_pushed == 0
    tasks['t1']['description'] = 'Book the big retro room'
    assert _run(settings).tasks_pushed == 1


def test_remote_completion_flips_the_task_once_and_foreign_todos_are_imported(world):
    settings, tasks, created, redis, stored = world
    _run(settings)
    client = _FakeClient.instances[-1]
    # the phone ticked t1 off, and added a to-do of its own
    client.objects[HOME + 'omi-tasks/omi-task-t1@omi-local.ics'] = ical.render_todo(
        uid='omi-task-t1@omi-local',
        summary='Book the retro room',
        completed=True,
        last_modified=NOW + timedelta(minutes=5),
        stamp=NOW,
    )
    client.objects[HOME + 'omi-tasks/phone-1.ics'] = ical.render_todo(
        uid='phone-1', summary='Buy milk', due=NOW + timedelta(days=1), last_modified=NOW, stamp=NOW
    )
    shared = client.objects
    _FakeClient.instances.clear()

    class _Persisted(_FakeClient):
        def __init__(self, *a):
            super().__init__(*a)
            self.objects = shared

    report = sync.sync_user('u1', settings, client_factory=_Persisted, now=NOW + timedelta(hours=1))
    assert tasks['t1']['completed'] is True and report.tasks_pulled == 1
    assert (
        report.tasks_imported == 1 and created[0][1]['description'] == 'Buy milk' and created[0][2] == 'caldav:phone-1'
    )
    assert tasks['new1']['source'] == 'caldav' and tasks['new1']['due_at'] == NOW + timedelta(days=1)
    # the same remote version is not applied again, and Omi's own flip is pushed back
    tasks['t1']['completed'] = False
    report = sync.sync_user('u1', settings, client_factory=_Persisted, now=NOW + timedelta(hours=2))
    assert report.tasks_pulled == 0 and tasks['t1']['completed'] is False
    assert 'STATUS:NEEDS-ACTION' in shared[HOME + 'omi-tasks/omi-task-t1@omi-local.ics']


def test_calendar_events_are_pulled_into_the_device_snapshot(world):
    settings, tasks, created, redis, stored = world
    _run(settings)
    client = _FakeClient.instances[-1]
    client.objects[HOME + 'personal/dentist.ics'] = ical.render_event(
        uid='dentist-42',
        summary='Dentist',
        start=NOW + timedelta(days=3),
        duration_minutes=60,
        location='Main St',
        stamp=NOW,
    )
    shared = client.objects

    class _Persisted(_FakeClient):
        def __init__(self, *a):
            super().__init__(*a)
            self.objects = shared

    report = sync.sync_user('u1', settings, client_factory=_Persisted, now=NOW)
    assert report.events_pulled == 1
    (event,) = stored['caldav']
    assert event['title'] == 'Dentist' and event['location'] == 'Main St' and event['account_type'] == 'caldav'
    assert event['end_ms'] - event['start_ms'] == 3600 * 1000


def test_toggles_and_put_failures(world):
    settings, tasks, created, redis, stored = world
    quiet = {
        **settings,
        'push_tasks': False,
        'pull_tasks': False,
        'push_day_summaries': False,
        'push_conversation_events': False,
        'pull_events': False,
    }
    assert _run(quiet).as_dict() == sync.SyncReport().as_dict()
    assert _run({**settings, 'enabled': False}).tasks_pushed == 0

    class _Flaky(_FakeClient):
        def __init__(self, *a):
            super().__init__(*a)
            self.fail_put = {HOME + 'omi-tasks/omi-task-t1@omi-local.ics'}

    report = sync.sync_user('u1', settings, client_factory=_Flaky, now=NOW)
    assert report.tasks_pushed == 1 and report.errors == ['task t1: PUT: HTTP 507']
    # the failed one is retried next time (no digest memo was written)
    assert redis.hget('caldav:pushed:u1', 't1') is None and redis.hget('caldav:pushed:u1', 't3')


def test_default_settings_come_from_the_bundled_server_env(monkeypatch):
    monkeypatch.delenv('CALDAV_DEFAULT_URL', raising=False)
    assert sync.default_settings() is None
    monkeypatch.setenv('CALDAV_DEFAULT_URL', 'http://omi-radicale:5232/')
    monkeypatch.setenv('CALDAV_DEFAULT_USERNAME', 'omi')
    monkeypatch.setenv('CALDAV_DEFAULT_PASSWORD', 'pw')
    default = sync.default_settings()
    assert default and default['bundled'] and default['url'] == 'http://omi-radicale:5232/' and default['push_tasks']
