"""utils/caldav/sync.py: what moves in which direction, and only when something changed."""

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

import database.caldav_settings as settings_db
from utils.caldav import ical
from utils.caldav import owners, sync
from utils.caldav.client import CalDavError, Calendar

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
HOME = 'http://dav.test/alice/'
WORLD = {}  # the world fixture's conversations and reviews, for the tests that change them
TAG = sync.owner_tag('u1')
TASKS = HOME + 'omi-tasks-alice/'
CAL = HOME + 'omi-alice/'
MADE = []  # collections MKCALENDAR made on the fake server (they are listed afterwards)


def task_href(base, task_id):
    return f'{base}omi-task-{TAG}-{task_id}@omi-local.ics'


def event_href(base, key):
    return f'{base}omi-event-{TAG}-{key}@omi-local.ics'


def day_href(base, date):
    return f'{base}omi-day-{TAG}-{date}@omi-local.ics'


def _etag(ics):
    """A server's ETag: changes with every different body, as Radicale's does."""
    return '"' + hashlib.sha256(ics.encode()).hexdigest()[:16] + '"'


class _FakeClient:
    """Records PUTs; serves whatever the test put into ``objects`` on REPORT."""

    instances = []

    def __init__(self, url, username, password):
        self.url, self.username, self.password = url, username, password
        self.puts = []
        self.made = []
        self.objects = {}  # href -> ics
        self.fail_put = set()
        self.deleted = []
        _FakeClient.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def calendar_home(self):
        return HOME

    def list_calendars(self, home=None):
        return [Calendar(href=HOME + 'personal/', name='Personal', components=['VEVENT']), *MADE]

    def make_calendar(self, href, name, components):
        self.made.append((href, name, list(components)))
        MADE.append(Calendar(href=href, name=name, components=list(components)))

    def put(self, href, ics):
        if href in self.fail_put:
            raise CalDavError('PUT: HTTP 507', 507)
        self.puts.append(href)
        self.objects[href] = ics
        return _etag(ics)

    def delete(self, href):
        self.deleted.append(href)
        self.objects.pop(href, None)

    def query(self, href, component, start=None, end=None):
        from utils.caldav.client import CalendarObject

        return [
            CalendarObject(href=h, etag=_etag(ics), data=ics)
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

    def hgetall(self, key):
        return dict(self.h.get(key, {}))

    def get(self, key):
        return self.h.get(key)

    def set(self, key, value):
        self.h[key] = value

    def delete(self, *keys):
        for key in keys:
            self.h.pop(key, None)

    def zadd(self, key, mapping):
        self.h.setdefault(key, {}).update(mapping)

    def zremrangebyrank(self, key, start, end):
        return 0

    def zrevrange(self, key, start, end):
        z = self.h.get(key, {})
        return sorted(z, key=lambda m: -z[m])


@pytest.fixture
def world(monkeypatch):
    _FakeClient.instances.clear()
    MADE.clear()
    monkeypatch.setattr(sync, 'owner_label', lambda uid: {'u1': 'Alice', 'u2': 'Bob'}.get(uid, uid))
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
                'events': [
                    {'title': 'Retro', 'start': (NOW + timedelta(days=2)).isoformat(), 'duration': 45, 'approved': True}
                ],
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
        sync.day_reviews_db, 'get_day_review', lambda uid, date: next((r for r in reviews if r['date'] == date), None)
    )
    monkeypatch.setattr(
        sync.conversations_db,
        'get_conversations_by_id_without_photos',
        lambda uid, ids, include_discarded=False: [dict(c) for c in conversations if c['id'] in ids],
    )
    monkeypatch.setattr(
        sync.device_calendar_db, 'store_events', lambda uid, events, source='device': stored.__setitem__(source, events)
    )
    by_id = {c['id']: c for c in conversations}
    monkeypatch.setattr(sync.conversations_db, 'get_conversation', lambda uid, cid: by_id.get(cid))
    monkeypatch.setattr(
        sync.conversations_db,
        'update_conversation_events',
        lambda uid, cid, events: by_id[cid]['structured'].__setitem__('events', events),
    )

    def update_task(uid, tid, *, description, due_at):
        if description is not None:
            tasks[tid]['description'] = description
        if due_at is not None:
            tasks[tid]['due_at'] = due_at

    monkeypatch.setattr(sync, '_update_task', update_task)
    monkeypatch.setattr(sync, '_delete_task', lambda uid, tid: tasks.pop(tid))
    WORLD.clear()
    WORLD.update(conversations=conversations, reviews=reviews)
    settings = {**settings_db.DEFAULTS, 'url': 'http://dav.test/', 'username': 'alice', 'password': 'pw'}
    return settings, tasks, created, redis, stored


def _run(settings):
    return sync.sync_user('u1', settings, client_factory=_FakeClient, now=NOW)


def test_first_sync_creates_calendars_and_pushes_tasks_summary_and_events(world):
    settings, tasks, created, redis, stored = world
    report = _run(settings)
    client = _FakeClient.instances[-1]
    # the user gets a calendar and a task list of their own, named after them
    assert client.made == [
        (HOME + 'omi-alice/', 'Omi – Alice', ['VEVENT']),
        (HOME + 'omi-tasks-alice/', 'Omi Tasks – Alice', ['VTODO']),
    ]
    assert report.collections == {'calendar_href': CAL, 'tasks_href': TASKS}
    assert report.tasks_pushed == 2  # t1 and the recently completed t3; t2 is older than the window
    assert report.events_pushed == 2  # the day summary and the extracted event; no conversation blocks by default
    assert report.errors == [] and report.tasks_pulled == 0 and report.tasks_imported == 0
    assert task_href(TASKS, 't1') in client.puts
    task = client.objects[task_href(TASKS, 't1')]
    assert 'SUMMARY:Book the retro room' in task and 'STATUS:NEEDS-ACTION' in task and 'DUE:20261029T120000Z' in task
    day = client.objects[day_href(CAL, '2026-10-26')]
    assert (
        'DTSTART;VALUE=DATE:20261026' in day
        and 'SUMMARY:Omi: Coffee and calls' in day
        and 'CATEGORIES:omi,coffee' in day
    )
    event = client.objects[event_href(CAL, 'c1-0')]
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
    client.objects[task_href(TASKS, 't1')] = ical.render_todo(
        uid=f'omi-task-{TAG}-t1@omi-local',
        summary='Book the retro room',
        completed=True,
        last_modified=NOW + timedelta(minutes=5),
        stamp=NOW,
    )
    client.objects[TASKS + 'phone-1.ics'] = ical.render_todo(
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
    assert 'STATUS:NEEDS-ACTION' in shared[task_href(TASKS, 't1')]


def test_calendar_events_are_pulled_into_the_device_snapshot(world):
    settings, tasks, created, redis, stored = world
    _run(settings)
    client = _FakeClient.instances[-1]
    client.objects[CAL + 'dentist.ics'] = ical.render_event(
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
            self.fail_put = {task_href(TASKS, 't1')}

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


class _TasksOnlyClient(_FakeClient):
    """Vikunja behind ALLGlance: task lists only, and MKCALENDAR answers 404."""

    def list_calendars(self, home=None):
        return [Calendar(href=HOME + 'projects/1/', name='Inbox', components=['VTODO'])]

    def make_calendar(self, href, name, components):
        self.made.append((href, name, list(components)))
        raise CalDavError('MKCALENDAR: HTTP 404', 404)


def test_tasks_only_server_syncs_tasks_and_says_what_it_left_out(world):
    settings, tasks, created, redis, stored = world
    report = sync.sync_user('u1', settings, client_factory=_TasksOnlyClient, now=NOW)
    client = _TasksOnlyClient.instances[-1]
    assert report.tasks_pushed == 2 and report.events_pushed == 0 and report.events_pulled == 0
    assert report.errors == [] and report.notes == [sync.TASKS_ONLY_NOTE]
    assert task_href(HOME + 'projects/1/', 't1') in client.puts
    # both were asked for once (and refused); the existing task list is used instead
    assert client.made == [
        (HOME + 'omi-alice/', 'Omi – Alice', ['VEVENT']),
        (HOME + 'omi-tasks-alice/', 'Omi Tasks – Alice', ['VTODO']),
    ]
    assert 'notes' in report.as_dict()


def test_with_every_event_direction_off_no_events_calendar_is_looked_for(world):
    settings, tasks, created, redis, stored = world
    quiet = {**settings, 'push_day_summaries': False, 'push_conversation_events': False, 'pull_events': False}
    report = sync.sync_user('u1', quiet, client_factory=_TasksOnlyClient, now=NOW)
    client = _TasksOnlyClient.instances[-1]
    assert [m for m in client.made if 'VEVENT' in m[2]] == [] and report.notes == [] and report.tasks_pushed == 2


def test_no_task_list_and_no_mkcalendar_is_a_clear_error(world):
    settings, tasks, created, redis, stored = world

    class _Empty(_TasksOnlyClient):
        def list_calendars(self, home=None):
            return []

    with pytest.raises(CalDavError) as caught:
        sync.sync_user(
            'u1',
            {**settings, 'pull_events': False, 'push_day_summaries': False, 'push_conversation_events': False},
            client_factory=_Empty,
            now=NOW,
        )
    assert 'create a list in the app' in str(caught.value)


# ---- both directions: deletes, edits and phone-made tasks --------------------------------------


@pytest.fixture
def server():
    """One server whose objects outlive each sync's client, as a real one does."""
    objects = {}

    class _Server(_FakeClient):
        def __init__(self, *a):
            super().__init__(*a)
            self.objects = objects

    return _Server, objects


def _sync(settings, client, at=NOW):
    return sync.sync_user('u1', settings, client_factory=client, now=at)


def _phone_edit(objects, href, **props):
    """Rewrite properties the way a phone app saves an edit (new LAST-MODIFIED)."""
    lines = objects[href].split('\r\n')
    for name, value in props.items():
        lines = [l for l in lines if not l.split(':', 1)[0].split(';', 1)[0] == name]
        if value is not None:
            lines.insert(lines.index(next(l for l in lines if l.startswith('UID:'))) + 1, f'{name}:{value}')
    objects[href] = '\r\n'.join(lines)


def test_a_task_deleted_in_omi_comes_off_the_server(world, server):
    settings, tasks, created, redis, stored = world
    client, objects = server
    _sync(settings, client)
    assert task_href(TASKS, 't1') in objects
    del tasks['t1']
    report = _sync(settings, client)
    assert report.tasks_removed == 1 and task_href(TASKS, 't1') not in objects
    assert redis.hget('caldav:remote:u1', 't1') is None and redis.hget('caldav:pushed:u1', 't1') is None
    # t2 (done long ago) was never pushed, so it is not in the memo and never "deleted"
    assert report.tasks_deleted == 0 and 't2' in tasks


def test_phone_rename_due_change_and_delete_reach_omi(world, server):
    settings, tasks, created, redis, stored = world
    client, objects = server
    _sync(settings, client)
    href = task_href(TASKS, 't1')
    _phone_edit(
        objects,
        href,
        SUMMARY='Book the big retro room',
        DUE=None,
        **{'DUE;TZID=Europe/Berlin': '20261101T090000', 'LAST-MODIFIED': '20261027T130000Z'},
    )
    report = _sync(settings, client, NOW + timedelta(hours=1))
    assert report.tasks_pulled == 1 and report.errors == []
    assert tasks['t1']['description'] == 'Book the big retro room'
    assert tasks['t1']['due_at'] == datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)  # 09:00 CET
    # Omi took the phone's version over: the next pass does not PUT it back
    assert _sync(settings, client, NOW + timedelta(hours=1, minutes=30)).tasks_pushed == 0
    # the phone deletes it: Omi deletes the task, and nothing pushes it back
    del objects[href]
    report = _sync(settings, client, NOW + timedelta(hours=2))
    assert report.tasks_deleted == 1 and 't1' not in tasks
    assert _sync(settings, client, NOW + timedelta(hours=3)).tasks_pushed == 0 and href not in objects


def test_an_empty_task_list_is_not_read_as_everything_deleted(world, server):
    settings, tasks, created, redis, stored = world
    client, objects = server
    _sync(settings, client)
    objects.clear()  # a wiped server, or the wrong collection
    report = _sync({**settings, 'push_tasks': False}, client)
    assert report.tasks_deleted == 0 and {'t1', 't3'} <= set(tasks)
    assert report.notes and 'nothing was deleted' in report.notes[0]


def test_a_phone_made_task_keeps_its_uid_and_is_not_pushed_back_as_a_copy(world, server):
    settings, tasks, created, redis, stored = world
    client, objects = server
    objects[TASKS + 'phone-1.ics'] = ical.render_todo(
        uid='phone-1', summary='Buy milk', due=NOW + timedelta(days=1), last_modified=NOW, stamp=NOW
    )
    report = _sync(settings, client)
    assert report.tasks_imported == 1
    tasks['new1']['updated_at'] = NOW
    report = _sync(settings, client, NOW + timedelta(hours=1))
    vtodos = [h for h, ics in objects.items() if 'SUMMARY:Buy milk' in ics]
    assert vtodos == [TASKS + 'phone-1.ics'], vtodos
    # done in Omi: the phone's own object is updated, under its own UID
    tasks['new1'].update(completed=True, completed_at=NOW + timedelta(hours=2), updated_at=NOW + timedelta(hours=2))
    _sync(settings, client, NOW + timedelta(hours=2))
    assert 'UID:phone-1' in objects[TASKS + 'phone-1.ics'] and 'STATUS:COMPLETED' in objects[TASKS + 'phone-1.ics']
    assert not any('-new1@' in h for h in objects)
    # and deleted in Omi: the phone's object goes
    del tasks['new1']
    assert _sync(settings, client, NOW + timedelta(hours=3)).tasks_removed == 1
    assert TASKS + 'phone-1.ics' not in objects and redis.hget('caldav:imported:u1', 'phone-1') is None


def test_declined_events_and_deleted_conversations_come_off_the_calendar(world, server):
    settings, tasks, created, redis, stored = world
    client, objects = server
    conversation = WORLD['conversations'][0]
    conversation['structured']['events'].append(
        {'title': 'Lunch', 'start': (NOW + timedelta(days=3)).isoformat(), 'duration': 60, 'approved': True}
    )
    _sync(settings, client)
    assert {event_href(CAL, 'c1-0'), event_href(CAL, 'c1-1')} <= set(objects)
    conversation['structured']['events'][1]['declined'] = True
    report = _sync(settings, client)
    assert report.events_removed == 1 and event_href(CAL, 'c1-1') not in objects
    assert event_href(CAL, 'c1-0') in objects
    WORLD['conversations'].clear()  # the conversation is deleted
    report = _sync(settings, client)
    assert report.events_removed == 1 and not any('-c1-' in h for h in objects)
    assert day_href(CAL, '2026-10-26') in objects  # the day summary stays


def test_a_conversation_being_reprocessed_keeps_its_events(world, server):
    settings, tasks, created, redis, stored = world
    client, objects = server
    _sync(settings, client)
    WORLD['conversations'][0]['status'] = 'processing'
    WORLD['conversations'][0]['structured']['events'] = []
    assert _sync(settings, client).events_removed == 0 and event_href(CAL, 'c1-0') in objects


def test_an_extracted_event_edited_or_deleted_on_the_phone_goes_back_to_omi(world, server):
    settings, tasks, created, redis, stored = world
    client, objects = server
    _sync(settings, client)
    href = event_href(CAL, 'c1-0')
    _phone_edit(
        objects,
        href,
        SUMMARY='Retro (moved)',
        DTSTART=None,
        DTEND=None,
        **{
            'DTSTART;TZID=Europe/Berlin': '20261029T150000',
            'DTEND;TZID=Europe/Berlin': '20261029T160000',
            'LAST-MODIFIED': '20261027T130000Z',
        },
    )
    report = _sync(settings, client, NOW + timedelta(hours=1))
    event = WORLD['conversations'][0]['structured']['events'][0]
    assert report.events_updated == 1 and event['title'] == 'Retro (moved)'
    assert event['start'] == datetime(2026, 10, 29, 14, 0, tzinfo=timezone.utc) and event['duration'] == 60
    # the next pass leaves the phone's object as the phone saved it, and reads nothing back
    phone_version = objects[href]
    report = _sync(settings, client, NOW + timedelta(hours=2))
    assert report.events_updated == 0 and report.events_pushed == 0 and objects[href] == phone_version
    # a later change in Omi is pushed over it, in Omi's shape
    WORLD['conversations'][0]['structured']['events'][0]['duration'] = 90
    report = _sync(settings, client, NOW + timedelta(hours=3))
    assert report.events_pushed == 1 and report.events_updated == 0
    assert 'DTSTART:20261029T140000Z' in objects[href] and 'DTEND:20261029T153000Z' in objects[href]
    # deleted on the phone: declined in Omi, and not pushed again
    del objects[href]
    report = _sync(settings, client, NOW + timedelta(hours=4))
    assert report.events_declined == 1 and WORLD['conversations'][0]['structured']['events'][0]['declined'] is True
    assert href not in objects and _sync(settings, client, NOW + timedelta(hours=5)).events_pushed == 0


def test_omi_wins_when_both_sides_changed_an_event(world, server):
    settings, tasks, created, redis, stored = world
    client, objects = server
    _sync(settings, client)
    href = event_href(CAL, 'c1-0')
    _phone_edit(objects, href, SUMMARY='Phone title', **{'LAST-MODIFIED': '20261027T130000Z'})
    WORLD['conversations'][0]['structured']['events'][0]['title'] = 'Omi title'
    report = _sync(settings, client, NOW + timedelta(hours=1))
    assert report.events_updated == 0 and 'SUMMARY:Omi title' in objects[href]
    assert WORLD['conversations'][0]['structured']['events'][0]['title'] == 'Omi title'


def test_pointing_the_sync_at_another_list_starts_the_memos_over(world, server):
    settings, tasks, created, redis, stored = world
    client, objects = server
    _sync(settings, client)
    moved = {**settings, 'tasks_href': HOME + 'other/'}
    report = _sync(moved, client)
    # nothing read as deleted, and every task is pushed to the new list
    assert report.tasks_deleted == 0 and {'t1', 't3'} <= set(tasks)
    assert report.tasks_pushed == 2 and task_href(HOME + 'other/', 't1') in objects


def test_a_phone_made_task_completed_on_the_phone_completes_in_omi(world, server):
    settings, tasks, created, redis, stored = world
    client, objects = server
    objects[TASKS + 'phone-1.ics'] = ical.render_todo(
        uid='phone-1', summary='Buy milk', due=NOW + timedelta(days=1), last_modified=NOW, stamp=NOW
    )
    _sync(settings, client)
    tasks['new1']['updated_at'] = NOW
    _sync(settings, client, NOW + timedelta(hours=1))
    _phone_edit(objects, TASKS + 'phone-1.ics', STATUS='COMPLETED', **{'LAST-MODIFIED': '20261027T150000Z'})
    report = _sync(settings, client, NOW + timedelta(hours=2))
    assert tasks['new1']['completed'] is True, (report, objects)


class _LockRedis(_Redis):
    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.h:
            return None
        self.h[key] = value
        return True

    def sadd(self, key, member):
        self.h.setdefault(key, set()).add(member)


def test_one_sync_per_user_at_a_time(world, monkeypatch):
    settings, tasks, created, redis, stored = world
    locking = _LockRedis()
    monkeypatch.setattr(sync.redis_db, 'r', locking)
    monkeypatch.setattr(sync, 'effective_settings', lambda uid: settings)
    monkeypatch.setattr(sync, 'CalDavClient', _FakeClient)
    recorded = []
    monkeypatch.setattr(sync.settings_db, 'record_sync', lambda uid, **k: recorded.append(k))
    monkeypatch.setattr(sync, 'sync_user', lambda uid, s: sync.SyncReport())
    locking.set(sync._lock_key('u1'), 'another-pass')
    # a scheduler pass gives up at once, and asks the next change pass to try again
    assert sync.run_for_user('u1') == {'error': sync.BUSY, 'busy': True} and recorded == []
    monkeypatch.setattr(sync, 'take_dirty', lambda: ['u1'])
    assert sync.run_dirty()['u1']['busy'] and 'u1' in locking.h[sync.DIRTY_KEY]
    # "Sync now" waits for it
    naps = []
    monkeypatch.setattr(sync.time, 'sleep', lambda s: (naps.append(s), locking.h.pop(sync._lock_key('u1'), None)))
    assert sync.run_for_user('u1', wait=5)['errors'] == [] and naps
    # and the lease is released afterwards, also when the sync fails
    assert sync._lock_key('u1') not in locking.h
    monkeypatch.setattr(sync, 'sync_user', lambda uid, s: (_ for _ in ()).throw(CalDavError('PROPFIND: HTTP 401', 401)))
    assert sync.run_for_user('u1') == {'error': 'PROPFIND: HTTP 401'} and sync._lock_key('u1') not in locking.h


def test_a_phone_edit_in_the_same_second_as_the_import_is_still_seen(world, server):
    """LAST-MODIFIED has whole seconds; the ETag tells two saves in one second apart."""
    settings, tasks, created, redis, stored = world
    client, objects = server
    href = TASKS + 'phone-1.ics'
    objects[href] = ical.render_todo(uid='phone-1', summary='Buy milk', last_modified=NOW, stamp=NOW)
    _sync(settings, client)
    _phone_edit(objects, href, STATUS='COMPLETED')  # same LAST-MODIFIED as at the import
    _sync(settings, client, NOW + timedelta(seconds=1))
    assert tasks['new1']['completed'] is True


def test_a_failed_push_is_not_read_back_as_the_phones_edit(world, server):
    settings, tasks, created, redis, stored = world
    client, objects = server
    _sync(settings, client)
    href = task_href(TASKS, 't1')
    redis.hset('caldav:remote:u1', 't1', '20261027T110000Z')  # a memo from before ETags were kept
    tasks['t1']['description'] = 'Renamed in Omi'

    class _Refusing(client):
        def __init__(self, *a):
            super().__init__(*a)
            self.fail_put = {href}

    report = _sync(settings, _Refusing)
    assert report.errors == ['task t1: PUT: HTTP 507'] and tasks['t1']['description'] == 'Renamed in Omi'
    assert 'unpushed' not in report.as_dict()
    # the next pass gets it through
    _sync(settings, client)
    assert 'SUMMARY:Renamed in Omi' in objects[href] and tasks['t1']['description'] == 'Renamed in Omi'


# ---- one CalDAV account, several Omi users -----------------------------------------------------


def _as(uid, settings, client, at=NOW):
    return sync.sync_user(uid, settings, client_factory=client, now=at)


def test_two_users_on_one_account_get_their_own_collections_and_same_date_summaries_do_not_collide(
    world, server, monkeypatch
):
    settings, tasks, created, redis, stored = world
    client, objects = server
    _as('u1', settings, client)
    _as('u2', settings, client)
    bob = sync.owner_tag('u2')
    # each got a calendar and a task list named after them
    assert {c.name for c in MADE} == {'Omi – Alice', 'Omi Tasks – Alice', 'Omi – Bob', 'Omi Tasks – Bob'}
    # the same day summary (same date) exists twice, under each owner's UID, in each one's calendar
    assert day_href(CAL, '2026-10-26') in objects
    assert f'{HOME}omi-bob/omi-day-{bob}-2026-10-26@omi-local.ics' in objects
    assert owners.collection_users(CAL) == {'u1': 'Alice'} and owners.collection_users(HOME + 'omi-bob/') == {
        'u2': 'Bob'
    }


def test_two_users_who_pick_the_same_calendar_still_do_not_overwrite_each_other(world, server):
    settings, tasks, created, redis, stored = world
    client, objects = server
    shared = {**settings, 'calendar_href': HOME + 'family/', 'tasks_href': HOME + 'family-tasks/'}
    _as('u1', shared, client)
    _as('u2', shared, client)
    days = sorted(h for h in objects if '-2026-10-26@' in h)
    assert days == sorted(
        [
            f'{HOME}family/omi-day-{TAG}-2026-10-26@omi-local.ics',
            f'{HOME}family/omi-day-{sync.owner_tag("u2")}-2026-10-26@omi-local.ics',
        ]
    )
    # Bob's pass left Alice's objects alone (nothing read as deleted, nothing imported)
    assert task_href(HOME + 'family-tasks/', 't1') in objects and created == []
    # and it says why phone-made tasks are not imported from a shared list
    assert sync.SHARED_LIST_NOTE in _as('u2', shared, client).notes


def test_a_user_who_synced_before_keeps_the_old_shared_collections_and_uids(world, server):
    """Migration: the binding memo from before owners were in UIDs keeps the user where they were.
    Their tasks and events keep their UIDs (nothing duplicated on the phone); the old shared
    day UID, which collided between users, is replaced by a scoped one."""
    settings, tasks, created, redis, stored = world
    client, objects = server
    old_tasks, old_cal = HOME + 'omi-tasks/', HOME + 'omi/'
    MADE.extend(
        [Calendar(href=old_cal, name='Omi', components=['VEVENT']), Calendar(old_tasks, 'Omi Tasks', ['VTODO'])]
    )
    redis.set('caldav:collections:u1', f'{old_tasks}|{old_cal}')  # what the old sync wrote
    legacy_task = old_tasks + 'omi-task-t1@omi-local.ics'
    objects[legacy_task] = sync.render_task({**tasks['t1']}, now=NOW, uid_value='omi-task-t1@omi-local')
    redis.hset('caldav:href:u1', 't1', legacy_task)
    redis.hset('caldav:remote:u1', 't1', _etag(objects[legacy_task]))
    redis.hset('caldav:pushed:u1', 't1', sync._digest(objects[legacy_task]))
    objects[old_cal + 'omi-day-2026-10-26@omi-local.ics'] = 'BEGIN:VCALENDAR\r\nEND:VCALENDAR'
    redis.hset('caldav:pushed:u1', 'day:2026-10-26', 'x')
    report = _sync({**settings}, client)
    assert report.collections == {'calendar_href': old_cal, 'tasks_href': old_tasks}
    assert not [m for m in MADE if m.name.endswith('Alice')]  # nothing new created
    # the task keeps its old UID: no second copy on the phone, nothing deleted in Omi
    assert legacy_task in objects and not any(f'-{TAG}-t1@' in h for h in objects) and 't1' in tasks
    assert report.tasks_deleted == 0
    # t3 had never been pushed: it goes up with the old (unscoped) UID too, as long as the user stays
    assert old_tasks + 'omi-task-t3@omi-local.ics' in objects
    # the shared omi-day-<date> is replaced by Alice's own
    assert old_cal + 'omi-day-2026-10-26@omi-local.ics' not in objects
    assert f'{old_cal}omi-day-{TAG}-2026-10-26@omi-local.ics' in objects


def test_an_old_shared_list_never_deletes_the_other_users_tasks(world, server):
    """Two users on the old shared "Omi Tasks" (unscoped UIDs): Alice's pass must not remember Bob's
    omi-task-<his id> as hers, and so never deletes it."""
    settings, tasks, created, redis, stored = world
    client, objects = server
    old_tasks = HOME + 'omi-tasks/'
    redis.set('caldav:collections:u1', f'{old_tasks}|{HOME}omi/')
    bobs = old_tasks + 'omi-task-bob1@omi-local.ics'
    objects[bobs] = ical.render_todo(uid='omi-task-bob1@omi-local', summary="Bob's thing", stamp=NOW)
    _sync(settings, client)
    _sync(settings, client, NOW + timedelta(hours=1))
    assert bobs in objects and redis.hget('caldav:remote:u1', 'bob1') is None and created == []


def test_switching_collections_from_the_picker_starts_over_and_deletes_nothing(world, server):
    settings, tasks, created, redis, stored = world
    client, objects = server
    _sync(settings, client)
    picked = {**settings, 'tasks_href': HOME + 'new-list/', 'calendar_href': HOME + 'new-cal/'}
    report = _sync(picked, client)
    assert report.tasks_deleted == 0 and report.events_declined == 0 and {'t1', 't3'} <= set(tasks)
    assert task_href(HOME + 'new-list/', 't1') in objects and day_href(HOME + 'new-cal/', '2026-10-26') in objects


def test_parent_mode_puts_tasks_under_the_users_parent_and_imports_only_its_subtasks(world, server):
    settings, tasks, created, redis, stored = world
    client, objects = server
    shared = {**settings, 'tasks_href': HOME + 'family-tasks/', 'task_mode': 'parent'}
    _as('u1', shared, client)
    parent_uid = f'omi-parent-{TAG}@omi-local'
    parent = objects[HOME + f'family-tasks/{parent_uid}.ics']
    assert "SUMMARY:Alice's tasks" in parent
    assert f'RELATED-TO;RELTYPE=PARENT:{parent_uid}' in objects[task_href(HOME + 'family-tasks/', 't1')]
    # on the phone: a subtask of Alice's parent, and a to-do of someone else's
    objects[HOME + 'family-tasks/phone-a.ics'] = ical.render_todo(
        uid='phone-a', summary='Buy milk', parent_uid=parent_uid, stamp=NOW
    )
    objects[HOME + 'family-tasks/phone-b.ics'] = ical.render_todo(
        uid='phone-b', summary='Not mine', parent_uid='omi-parent-u00000000@omi-local', stamp=NOW
    )
    objects[HOME + 'family-tasks/phone-c.ics'] = ical.render_todo(uid='phone-c', summary='Loose one', stamp=NOW)
    report = _as('u1', shared, client, NOW + timedelta(hours=1))
    assert report.tasks_imported == 1 and [c[1]['description'] for c in created] == ['Buy milk']
    assert sync.SHARED_LIST_NOTE not in report.notes


# ---- approval: nothing reaches the server before the user's OK -----------------------------------


def test_a_task_waiting_for_approval_is_not_pushed_until_approved_and_a_decline_keeps_it_off(world, server):
    settings, tasks, created, redis, stored = world
    client, objects = server
    tasks['t4'] = {'id': 't4', 'description': 'Heard: call the plumber', 'completed': False, 'approval': 'pending'}
    tasks['t5'] = {'id': 't5', 'description': 'Heard: no', 'completed': False, 'approval': 'declined'}
    _sync(settings, client)
    assert task_href(TASKS, 't4') not in objects and task_href(TASKS, 't5') not in objects
    tasks['t4']['approval'] = 'approved'
    assert _sync(settings, client).tasks_pushed == 1 and task_href(TASKS, 't4') in objects
    # one that leaves the approved state (never through the app; a data repair) comes off again
    tasks['t4']['approval'] = 'declined'
    report = _sync(settings, client)
    assert report.tasks_removed == 1 and task_href(TASKS, 't4') not in objects and 't4' in tasks


def test_an_extracted_event_waits_for_approval_and_one_pushed_before_approvals_is_kept(world, server):
    settings, tasks, created, redis, stored = world
    client, objects = server
    events = WORLD['conversations'][0]['structured']['events']
    events[0].pop('approved')
    _sync(settings, client)
    assert event_href(CAL, 'c1-0') not in objects
    events[0]['approved'] = True
    _sync(settings, client)
    assert event_href(CAL, 'c1-0') in objects
    # an event pushed by the old sync (memo, no approval field) stays and is marked approved
    events.append({'title': 'Old one', 'start': (NOW + timedelta(days=4)).isoformat(), 'duration': 30})
    redis.hset('caldav:pushed:u1', 'event:c1-1', 'old-digest')
    _sync(settings, client)
    assert event_href(CAL, 'c1-1') in objects
    assert WORLD['conversations'][0]['structured']['events'][1]['approved'] is True


def test_an_event_approved_before_its_day_is_reviewed_is_pushed(world, server):
    settings, tasks, created, redis, stored = world
    client, objects = server
    WORLD['reviews'].clear()  # the day has no review yet
    _sync(settings, client)
    assert event_href(CAL, 'c1-0') not in objects  # the sync did not know the conversation
    sync.remember_approved_event('u1', 'c1')  # what the Calendar check's approve does
    _sync(settings, client)
    assert event_href(CAL, 'c1-0') in objects


def test_a_draft_day_summary_is_not_pushed_and_reopening_takes_it_off(world, server):
    settings, tasks, created, redis, stored = world
    client, objects = server
    review = WORLD['reviews'][0]
    review['status'] = 'draft'
    _sync(settings, client)
    assert day_href(CAL, '2026-10-26') not in objects
    review['status'] = 'finalized'  # approved
    _sync(settings, client)
    assert day_href(CAL, '2026-10-26') in objects
    review['status'] = 'draft'  # reopened
    report = _sync(settings, client)
    assert day_href(CAL, '2026-10-26') not in objects and report.events_removed == 1
    WORLD['reviews'].clear()  # a reopened draft discarded: nothing comes back
    assert _sync(settings, client).events_pushed == 0


# ---- collections API and settings ----------------------------------------------------------------


def test_collections_list_marks_mine_and_other_peoples(world, server, monkeypatch):
    settings, tasks, created, redis, stored = world
    client, objects = server
    _as('u2', settings, client)  # Bob syncs first
    _as('u1', settings, client)
    listed = sync.list_collections('u1', {**settings, 'calendar_href': CAL, 'tasks_href': TASKS}, client_factory=client)
    by_name = {c['name']: c for c in listed}
    assert by_name['Omi – Alice']['mine'] == ['calendar'] and by_name['Omi Tasks – Alice']['mine'] == ['tasks']
    assert by_name['Omi – Bob']['used_by'] == ['Bob'] and by_name['Omi – Alice']['used_by'] == []
    made = sync.create_collection('u1', settings, 'Family', 'tasks', client_factory=client)
    assert made['href'] == HOME + 'family/' and made['components'] == ['VTODO']
    # the same name again gets a free href
    assert (
        sync.create_collection('u1', settings, 'Family', 'tasks', client_factory=client)['href'] == HOME + 'family-2/'
    )


def test_bundled_server_applies_the_users_own_choices(monkeypatch):
    monkeypatch.setenv('CALDAV_DEFAULT_URL', 'http://omi-radicale:5232/')
    monkeypatch.setenv('CALDAV_DEFAULT_USERNAME', 'omi')
    monkeypatch.setenv('CALDAV_DEFAULT_PASSWORD', 'pw')
    monkeypatch.setattr(
        sync.settings_db,
        'get_settings',
        lambda uid: {**settings_db.DEFAULTS, 'tasks_href': '/omi/family/', 'task_mode': 'parent', 'pull_events': False},
    )
    effective = sync.effective_settings('u1')
    assert effective['url'] == 'http://omi-radicale:5232/' and effective['bundled']
    assert effective['tasks_href'] == '/omi/family/' and effective['task_mode'] == 'parent'
    assert effective['pull_events'] is False


def test_owner_tags_never_read_as_an_old_uid():
    tag = sync.owner_tag('u1')
    assert sync.parse_owned('omi-event-', f'omi-event-{tag}-c1-0@omi-local', tag) == ('mine', 'c1-0')
    # an old UID whose conversation id is a uuid (8 hex, then a dash) stays unscoped
    old = 'omi-event-3f2a1b4c-aaaa-4bbb-8ccc-0123456789ab-0@omi-local'
    assert sync.parse_owned('omi-event-', old, tag) == ('mine', '3f2a1b4c-aaaa-4bbb-8ccc-0123456789ab-0')
    assert sync.parse_owned('omi-event-', f'omi-event-{sync.owner_tag("u2")}-c1-0@omi-local', tag) == (
        'foreign',
        None,
    )
