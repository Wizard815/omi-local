"""omi-local: a re-extracted open task becomes no new task (utils/conversations/task_merge.py),
and a merge never bypasses the user's approval (utils/task_approval.py)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from utils import task_approval
from utils.conversations import task_merge

NOW = datetime(2026, 11, 20, 18, tzinfo=timezone.utc)
FRIDAY = datetime(2026, 11, 27, 17, tzinfo=timezone.utc)
SATURDAY = datetime(2026, 11, 28, 17, tzinfo=timezone.utc)

# Scores measured with text-embedding-3-small (the self-hosted embedding model).
SCORES = {
    ('Venmo Gary for the completed plumbing work', 'venmo'): 0.947,
    ('Put Lily\'s $15 science museum field trip cash in her folder', 'folder'): 0.801,
    ('Book a hotel in Denver for the conference', 'flights'): 0.79,  # stand-in below threshold
    ('Pack a lunch for the science museum trip', 'folder'): 0.544,
    ('Let Gary the plumber in', 'venmo'): 0.691,
}


@pytest.fixture
def store(monkeypatch):
    rows = {
        'venmo': {
            'id': 'venmo',
            'description': 'Venmo Gary for the plumbing work and parts',
            'conversation_id': 'monday',
            'approval': 'pending',
            'completed': False,
            'created_at': NOW - timedelta(days=1),
        },
        'folder': {
            'id': 'folder',
            'description': "Put fifteen dollars in Lily's folder",
            'conversation_id': 'monday',
            'approval': 'approved',
            'completed': False,
            'created_at': NOW - timedelta(days=1),
            'due_at': FRIDAY,
        },
        'flights': {
            'id': 'flights',
            'description': 'Book flights to Denver for the conference',
            'conversation_id': 'monday',
            'completed': False,
            'created_at': NOW - timedelta(days=1),
        },
    }
    own = []
    deleted = []
    reminders = []

    def similar(uid, query, threshold=0.0, limit=5):
        return [{'action_item_id': rid, 'score': s} for (q, rid), s in SCORES.items() if q == query]

    monkeypatch.setattr(
        task_merge.action_items_db,
        'get_action_items_by_ids',
        lambda uid, ids: [dict(rows[i]) for i in ids if i in rows],
    )
    monkeypatch.setattr(task_merge.action_items_db, 'get_action_items_by_conversation', lambda uid, cid: list(own))
    monkeypatch.setattr(task_merge.action_items_db, 'update_action_item', lambda uid, rid, d: rows[rid].update(d))
    monkeypatch.setattr(
        task_merge.conversations_db,
        'get_conversation',
        lambda uid, cid: {'started_at': NOW - timedelta(days=1)} if cid == 'monday' else None,
    )
    monkeypatch.setattr(task_approval.action_items_db, 'get_action_item', lambda uid, rid: rows.get(rid))
    monkeypatch.setattr(task_approval.action_items_db, 'update_action_item', lambda uid, rid, d: rows[rid].update(d))
    monkeypatch.setattr(
        task_approval.action_items_db, 'delete_action_item', lambda uid, rid: deleted.append(rid) or rows.pop(rid)
    )
    monkeypatch.setattr(task_approval, 'dispatch_action_item_reminder', lambda **k: reminders.append(k))
    monkeypatch.setattr(task_approval, '_deliver', lambda *a: None)
    monkeypatch.setenv('OMI_TASK_APPROVAL', 'on')
    return SimpleNamespace(rows=rows, own=own, deleted=deleted, reminders=reminders, similar=similar)


def _tuesday():
    return SimpleNamespace(id='tuesday', started_at=NOW, external_data=None)


def _run(store, items, **kw):
    return task_merge.merge_into_open_tasks('u1', _tuesday(), items, now=NOW, similar=store.similar, **kw)


def test_normalize_ignores_case_punctuation_and_articles():
    assert task_merge.normalize('Call the Plumber!') == task_merge.normalize('call plumber')
    assert task_merge.normalize("Get Emma's shoes") == task_merge.normalize('get emma shoes')


def test_repeat_of_waiting_task_is_dropped_and_recorded(store):
    out = _run(store, [{'description': 'Venmo Gary for the completed plumbing work', 'due_at': None}])
    assert out.kept == []
    assert out.merged[0]['into'] == 'venmo' and out.merged[0]['action'] == 'dropped'
    assert store.rows['venmo']['merged_from'][0]['conversation_id'] == 'tuesday'
    assert store.rows['venmo']['approval'] == 'pending'  # still waits for the user


def test_unrelated_and_below_threshold_items_are_kept(store):
    items = [
        {'description': 'Pack a lunch for the science museum trip'},
        {'description': 'Let Gary the plumber in'},
        {'description': 'Book a hotel in Denver for the conference'},
    ]
    out = _run(store, items)
    assert out.kept == items and out.merged == []


def test_exact_text_merges_even_with_a_low_vector_score(store):
    def low(uid, query, threshold=0.0, limit=5):
        return [{'action_item_id': 'flights', 'score': 0.2}]

    out = task_merge.merge_into_open_tasks(
        'u1', _tuesday(), [{'description': 'book flights to Denver for conference.'}], now=NOW, similar=low
    )
    assert out.kept == [] and out.merged[0]['into'] == 'flights'


def test_later_due_date_moves_onto_a_waiting_task(store):
    out = _run(store, [{'description': 'Venmo Gary for the completed plumbing work', 'due_at': SATURDAY}])
    assert out.kept == [] and out.merged[0]['action'] == 'due_moved'
    assert store.rows['venmo']['due_at'] == SATURDAY and store.rows['venmo']['approval'] == 'pending'


def test_due_change_to_an_approved_task_waits_and_approving_moves_it(store):
    item = {'description': "Put Lily's $15 science museum field trip cash in her folder", 'due_at': SATURDAY}
    out = _run(store, [item])
    assert store.rows['folder']['due_at'] == FRIDAY  # nothing changed without the user's OK
    (update,) = out.kept
    assert update['updates_task_id'] == 'folder' and update['description'] == "Put fifteen dollars in Lily's folder"
    task_approval.mark_extracted(out.kept)
    assert update['approval'] == 'pending'

    store.rows['upd'] = {**update, 'id': 'upd'}
    result = task_approval.approve('u1', 'upd')
    assert result['id'] == 'folder' and store.rows['folder']['due_at'] == SATURDAY
    assert store.deleted == ['upd'] and store.reminders[0]['action_item_id'] == 'folder'


def test_same_due_on_approved_task_is_dropped(store):
    item = {'description': "Put Lily's $15 science museum field trip cash in her folder", 'due_at': FRIDAY}
    out = _run(store, [item])
    assert out.kept == [] and out.merged[0]['action'] == 'dropped'


def test_older_conversation_does_not_move_a_due_date(store):
    old = SimpleNamespace(id='sunday', started_at=NOW - timedelta(days=3), external_data=None)
    item = {'description': 'Venmo Gary for the completed plumbing work', 'due_at': SATURDAY}
    out = task_merge.merge_into_open_tasks('u1', old, [item], now=NOW, similar=store.similar)
    assert out.merged[0]['action'] == 'dropped' and 'due_at' not in store.rows['venmo']


def test_completed_declined_old_and_own_conversation_tasks_are_not_targets(store):
    item = {'description': 'Venmo Gary for the completed plumbing work'}
    for change in ({'completed': True}, {'approval': 'declined'}, {'created_at': NOW - timedelta(days=30)}):
        saved = dict(store.rows['venmo'])
        store.rows['venmo'].update(change)
        assert _run(store, [item]).kept == [item]
        store.rows['venmo'] = saved
    reprocess = SimpleNamespace(id='monday', started_at=NOW, external_data=None)
    out = task_merge.merge_into_open_tasks('u1', reprocess, [item], now=NOW, similar=store.similar)
    assert out.kept == [item]


def test_item_this_conversation_already_wrote_is_kept(store):
    store.own.append({'id': 'mine', 'description': 'Venmo Gary for the completed plumbing work'})
    item = {'description': 'Venmo Gary for the completed plumbing work'}
    assert _run(store, [item]).kept == [item]


def test_switch_off_and_vector_failure_keep_everything(store, monkeypatch):
    item = {'description': 'Venmo Gary for the completed plumbing work'}
    assert _run(store, [item], environ={'OMI_TASK_MERGE': 'off'}).kept == [item]

    def broken(*a, **k):
        raise RuntimeError('chroma down')

    monkeypatch.setattr(task_merge, 'record_fallback', lambda **k: None)
    out = task_merge.merge_into_open_tasks('u1', _tuesday(), [item], now=NOW, similar=broken)
    assert out.kept == [item]


def test_threshold_and_days_knobs():
    assert task_merge.merge_threshold({}) == 0.80
    assert task_merge.merge_threshold({'OMI_TASK_MERGE_THRESHOLD': '0.9'}) == 0.9
    assert task_merge.merge_threshold({'OMI_TASK_MERGE_THRESHOLD': 'x'}) == 0.80
    assert task_merge.merge_days({'OMI_TASK_MERGE_DAYS': '3'}) == 3
