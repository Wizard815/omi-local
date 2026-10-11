"""Nested conversation folders: folders carry ``parent_id`` and form a tree.

Seams: utils.folder_tree is pure; the router/database path runs against the
fake Firestore from test_folder_move_deleted_folder (update() on a missing doc
raises NotFound, like the real server).
"""

from unittest.mock import patch

import pytest
from fastapi import HTTPException

import database.folders as folders
import routers.folders as folders_router
from models.folder import CreateFolderRequest, UpdateFolderRequest
from tests.unit.test_folder_move_deleted_folder import _FakeDb, _FakeDocRef

# create_folder writes with set(); the shared fake only needed update().
_FakeDocRef.set = lambda self, values: self._store.__setitem__(self.id, dict(values))
from utils import folder_tree
from utils.llm.conversation_folder import build_folders_context

TREE = [
    {'id': 'work', 'name': 'Work', 'parent_id': None, 'is_system': True},
    {'id': 'clients', 'name': 'Clients', 'parent_id': 'work'},
    {'id': 'acme', 'name': 'Acme', 'parent_id': 'clients'},
    {'id': 'home', 'name': 'Home'},
    {'id': 'stray', 'name': 'Stray', 'parent_id': 'deleted-long-ago'},
]


def test_paths_children_and_descendants():
    assert folder_tree.path_names(TREE)['acme'] == 'Work / Clients / Acme'
    assert [f['id'] for f in folder_tree.children(TREE, None)] == ['work', 'home', 'stray']  # dangling parent → top
    assert folder_tree.descendant_ids(TREE, 'work') == ['clients', 'acme']
    assert folder_tree.depth(TREE, 'acme') == 3


def test_a_folder_cannot_move_inside_itself():
    with pytest.raises(folder_tree.FolderTreeError):
        folder_tree.validate_parent(TREE, 'acme', moving_folder_id='work')
    with pytest.raises(folder_tree.FolderTreeError):
        folder_tree.validate_parent(TREE, 'work', moving_folder_id='work')
    folder_tree.validate_parent(TREE, 'home', moving_folder_id='clients')  # allowed


def test_depth_limit_counts_the_moved_subtree():
    chain = [{'id': f'f{i}', 'name': f'F{i}', 'parent_id': f'f{i - 1}' if i else None} for i in range(4)]
    folder_tree.validate_parent(chain, 'f3')  # creating at depth 5 is fine
    with pytest.raises(folder_tree.FolderTreeError):
        folder_tree.validate_parent(chain + [{'id': 'leaf', 'name': 'L', 'parent_id': 'f3'}], 'leaf')
    tree = chain + [{'id': 'x', 'name': 'X'}, {'id': 'y', 'name': 'Y', 'parent_id': 'x'}]
    with pytest.raises(folder_tree.FolderTreeError):
        folder_tree.validate_parent(tree, 'f3', moving_folder_id='x')  # x+y would end at depth 6


def test_ai_folder_assignment_sees_full_paths():
    context = build_folders_context(TREE)
    assert '- acme | "Work / Clients / Acme"' in context


def _store(*folder_docs):
    return {
        f['id']: {k: v for k, v in f.items()} | {'order': i, 'conversation_count': 0} for i, f in enumerate(folder_docs)
    }


def test_create_inside_a_folder_and_reject_a_missing_parent():
    store = _store({'id': 'work', 'name': 'Work', 'is_system': True})
    with patch.object(folders, 'db', _FakeDb({}, store)):
        created = folders_router.create_folder(CreateFolderRequest(name='Clients', parent_id='work'), uid='u1')
        assert created['parent_id'] == 'work'
        with pytest.raises(HTTPException) as exc:
            folders_router.create_folder(CreateFolderRequest(name='Orphan', parent_id='nope'), uid='u1')
    assert exc.value.status_code == 400


def test_move_rules_through_the_router():
    store = _store(
        {'id': 'work', 'name': 'Work', 'is_system': True},
        {'id': 'clients', 'name': 'Clients', 'parent_id': 'work'},
        {'id': 'home', 'name': 'Home'},
    )
    with patch.object(folders, 'db', _FakeDb({}, store)):
        moved = folders_router.update_folder('clients', UpdateFolderRequest(parent_id='home'), uid='u1')
        assert moved['parent_id'] == 'home'
        # Explicit null moves to the top level.
        top = folders_router.update_folder('clients', UpdateFolderRequest.model_validate({'parent_id': None}), uid='u1')
        assert top['parent_id'] is None
        with pytest.raises(HTTPException) as cycle:
            folders_router.update_folder('home', UpdateFolderRequest(parent_id='home'), uid='u1')
        with pytest.raises(HTTPException) as system:
            folders_router.update_folder('work', UpdateFolderRequest(parent_id='home'), uid='u1')
        # A rename without parent_id leaves the parent alone.
        folders_router.update_folder('clients', UpdateFolderRequest(name='Customers'), uid='u1')
    assert cycle.value.status_code == 400
    assert system.value.status_code == 400
    assert store['clients']['name'] == 'Customers' and store['clients']['parent_id'] is None


def test_deleting_a_folder_moves_its_subfolders_up_one_level():
    store = _store(
        {'id': 'work', 'name': 'Work'},
        {'id': 'clients', 'name': 'Clients', 'parent_id': 'work'},
        {'id': 'acme', 'name': 'Acme', 'parent_id': 'clients'},
    )
    conversations = {'c1': {'folder_id': 'clients', 'discarded': False}}
    with patch.object(folders, 'db', _FakeDb(conversations, store)):
        folders.delete_folder('u1', 'clients', move_to_folder_id='work')
    assert 'clients' not in store
    assert store['acme']['parent_id'] == 'work'
    assert conversations['c1']['folder_id'] == 'work'
