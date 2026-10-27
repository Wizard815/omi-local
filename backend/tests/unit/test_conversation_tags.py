"""Conversation tags: normalization, the setter's catalog bookkeeping, and the
tag listing query. A tiny Firestore fake (document get/update with Increment,
array_contains/== filters, order_by/limit) stands in for the emulator."""

from datetime import datetime, timezone

import pytest
from google.cloud import firestore

import database.conversation_tags as catalog
import database.conversations as conversations_db
from utils.conversations.tags import normalize_tag, normalize_tags


def _merge(target, value):
    """Firestore set(merge=True) semantics for nested maps and Increment sentinels."""
    for key, item in value.items():
        if isinstance(item, dict):
            target[key] = _merge(dict(target.get(key) or {}), item)
        elif isinstance(item, firestore.Increment):
            target[key] = target.get(key, 0) + item.value
        else:
            target[key] = item
    return target


class _Snap:
    def __init__(self, path, data):
        self.id = path.rsplit('/', 1)[-1]
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class _Doc:
    def __init__(self, db, path):
        self.db, self.path = db, path

    def get(self, *a, **k):
        return _Snap(self.path, self.db.docs.get(self.path))

    def collection(self, name):
        return _Query(self.db, f'{self.path}/{name}')

    def set(self, value, merge=False):
        current = self.db.docs.get(self.path) if merge else None
        self.db.docs[self.path] = _merge(dict(current or {}), value)

    def update(self, values):
        doc = self.db.docs[self.path]
        for key, value in values.items():
            parts = key.split('.')
            target = doc
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            if isinstance(value, firestore.Increment):
                target[parts[-1]] = target.get(parts[-1], 0) + value.value
            else:
                target[parts[-1]] = value


class _Query:
    def __init__(self, db, path, filters=(), order=None, desc=False, limit=None):
        self.db, self.path, self.filters, self.order, self.desc, self.limit_n = db, path, filters, order, desc, limit

    def document(self, doc_id):
        return _Doc(self.db, f'{self.path}/{doc_id}')

    def where(self, filter):
        return _Query(self.db, self.path, (*self.filters, filter), self.order, self.desc, self.limit_n)

    def order_by(self, field, direction=None):
        return _Query(self.db, self.path, self.filters, field, direction == firestore.Query.DESCENDING, self.limit_n)

    def limit(self, n):
        return _Query(self.db, self.path, self.filters, self.order, self.desc, n)

    def stream(self):
        rows = []
        for path, data in self.db.docs.items():
            if not path.startswith(self.path + '/') or '/' in path[len(self.path) + 1 :]:
                continue
            ok = True
            for f in self.filters:
                actual = data.get(f.field_path)
                if f.op_string == '==':
                    ok &= actual == f.value
                elif f.op_string == 'array_contains':
                    ok &= f.value in (actual or [])
                else:
                    raise AssertionError(f.op_string)
            if ok:
                rows.append(_Snap(path, data))
        if self.order:
            rows.sort(key=lambda s: s.to_dict()[self.order], reverse=self.desc)
        return rows[: self.limit_n] if self.limit_n else rows


class _Db:
    def __init__(self):
        self.docs = {}

    def collection(self, path):
        return _Query(self, path)


@pytest.fixture
def db(monkeypatch):
    fake = _Db()
    monkeypatch.setattr(conversations_db, 'db', fake)
    monkeypatch.setattr(catalog, 'db', fake)
    base = datetime(2026, 10, 27, tzinfo=timezone.utc)
    for i, (cid, tags, discarded) in enumerate(
        [('c1', ['work'], False), ('c2', ['work', 'family'], False), ('c3', ['work'], True), ('c4', [], False)]
    ):
        fake.docs[f'users/u1/conversations/{cid}'] = {
            'id': cid,
            'tags': tags,
            'discarded': discarded,
            'created_at': base.replace(hour=i),
            'transcript_segments': [{'text': 'x'}],
        }
    fake.docs['users/u1/conversation_tags/catalog'] = {'counts': {'work': 3, 'family': 1}}
    return fake


def test_normalization_is_one_rule():
    assert normalize_tag('  Work / Projects  ') == 'work-projects'
    assert normalize_tag('#FAMILY,dinner') == 'family-dinner'
    assert normalize_tags(['Work', 'work', ' WORK ', '', 'x' * 60]) == ['work', 'x' * 40]
    assert len(normalize_tags([f't{i}' for i in range(40)])) == 20


def test_set_tags_updates_the_row_and_the_catalog(db):
    added, removed = conversations_db.set_conversation_tags('u1', 'c2', ['family', 'travel'])
    assert (added, removed) == (['travel'], ['work'])
    assert db.docs['users/u1/conversations/c2']['tags'] == ['family', 'travel']
    assert catalog.get_tag_catalog('u1') == [
        {'tag': 'work', 'count': 2},
        {'tag': 'family', 'count': 1},
        {'tag': 'travel', 'count': 1},
    ]


def test_set_tags_on_a_missing_conversation_changes_nothing(db):
    assert conversations_db.set_conversation_tags('u1', 'nope', ['work']) == ([], [])
    assert db.docs['users/u1/conversation_tags/catalog']['counts']['work'] == 3


def test_catalog_starts_from_nothing(db):
    db.docs.pop('users/u1/conversation_tags/catalog')
    assert catalog.get_tag_catalog('u1') == []
    conversations_db.set_conversation_tags('u1', 'c4', ['new'])
    assert catalog.get_tag_catalog('u1') == [{'tag': 'new', 'count': 1}]


def test_list_by_tag_is_newest_first_and_hides_discarded_unless_asked(db):
    rows = conversations_db.get_conversations_by_tag('u1', 'work')
    assert [r['id'] for r in rows] == ['c2', 'c1']
    assert 'transcript_segments' not in rows[0]
    assert [r['id'] for r in conversations_db.get_conversations_by_tag('u1', 'work', include_discarded=True)] == [
        'c3',
        'c2',
        'c1',
    ]
    assert conversations_db.get_conversations_by_tag('u1', 'nothing') == []
