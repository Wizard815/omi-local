"""Unit tests for edit_tools and turn_refs configuration resilience and input validation.

Asserts that owner extraction, turn reference resolution, and edit tools never crash
with unhandled AttributeErrors on None or missing configurable dictionaries, and that
rename_person validates target names before dispatching to MCP handlers.
"""

import importlib.util
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock
import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

_STUB_MODULES = [
    'database',
    'database.conversations',
    'database.users',
    'database._client',
    'database.chat_edits',
    'utils.memory',
    'utils.memory.memory_service',
    'utils.memory.memory_system',
    'utils.memory.canonical_memory_adapter',
    'utils.messaging',
    'utils.messaging.undo',
    'utils.mcp_server',
    'utils.mcp_server.handlers',
    'utils.mcp_server.handlers.other',
]

edit_tools = None
turn_refs = None


@pytest.fixture(scope='module', autouse=True)
def _isolated_edit_tools():
    """Stub heavy deps and load edit_tools for this module only, then restore sys.modules."""
    global edit_tools, turn_refs
    with pytest.MonkeyPatch.context() as mp:
        for mod in _STUB_MODULES:
            if mod not in sys.modules:
                mp.setitem(sys.modules, mod, types.ModuleType(mod))
        mp.setattr(sys.modules['database._client'], 'get_data_plane_firestore_client', MagicMock(), raising=False)
        mp.setattr(
            sys.modules['database.chat_edits'],
            'envelope',
            lambda item, label: {'item': item, 'label': label},
            raising=False,
        )
        mp.setattr(sys.modules['database.chat_edits'], 'snapshot', MagicMock(), raising=False)
        mp.setattr(sys.modules['database.chat_edits'], 'decode', MagicMock(), raising=False)
        mp.setattr(sys.modules['utils.messaging.undo'], 'record_write', MagicMock(), raising=False)
        mp.setattr(
            sys.modules['utils.mcp_server.handlers.other'],
            'rename_person',
            MagicMock(return_value={'person': {'name': 'New'}}),
            raising=False,
        )
        mp.setattr(sys.modules['utils.memory.memory_service'], 'MemoryService', MagicMock(), raising=False)
        mp.setattr(sys.modules['utils.memory.memory_system'], 'MemorySystem', MagicMock(), raising=False)
        mp.setattr(
            sys.modules['utils.memory.canonical_memory_adapter'],
            'read_canonical_memory_item',
            MagicMock(),
            raising=False,
        )

        edit_tools_path = BACKEND_DIR / 'utils' / 'retrieval' / 'tools' / 'edit_tools.py'
        spec = importlib.util.spec_from_file_location('utils.retrieval.tools.edit_tools', edit_tools_path)
        module = importlib.util.module_from_spec(spec)
        mp.setitem(sys.modules, 'utils.retrieval.tools.edit_tools', module)
        spec.loader.exec_module(module)

        edit_tools = module
        turn_refs = importlib.import_module('utils.retrieval.turn_refs')
        yield
    edit_tools = None
    turn_refs = None


class TestOwnerResilience:
    def test_owner_none_config_raises_permission_error(self):
        with pytest.raises(PermissionError, match="Authenticated owner required"):
            edit_tools.owner(None)

    def test_owner_empty_config_raises_permission_error(self):
        with pytest.raises(PermissionError, match="Authenticated owner required"):
            edit_tools.owner({})

    def test_owner_none_configurable_raises_permission_error(self):
        # When configurable is explicitly None, must raise PermissionError, NOT AttributeError
        with pytest.raises(PermissionError, match="Authenticated owner required"):
            edit_tools.owner({"configurable": None})

    def test_owner_empty_configurable_raises_permission_error(self):
        with pytest.raises(PermissionError, match="Authenticated owner required"):
            edit_tools.owner({"configurable": {}})

    def test_owner_blank_user_id_raises_permission_error(self):
        with pytest.raises(PermissionError, match="Authenticated owner required"):
            edit_tools.owner({"configurable": {"user_id": ""}})

    def test_owner_valid_user_id_returns_uid(self):
        assert edit_tools.owner({"configurable": {"user_id": "user_123"}}) == "user_123"


class TestTurnRefsResilience:
    def test_initialize_turn_refs_handles_none(self):
        refs = turn_refs.initialize_turn_refs(None)
        assert isinstance(refs, turn_refs.TurnRefs)
        assert len(refs.seen) == 0

    def test_register_record_ref_handles_none_configurable(self):
        # Must return cleanly without raising AttributeError
        turn_refs.register_record_ref(None, "conversations", "conv_123")

    def test_resolve_edit_ref_handles_none_configurable(self):
        target, error = turn_refs.resolve_edit_ref(
            None,
            "conversations",
            "conv_123",
            parameter="conversation_id",
        )
        assert target == "conv_123"
        assert error is None

    def test_resolve_edit_ref_requires_target_when_missing(self):
        target, error = turn_refs.resolve_edit_ref(
            None,
            "conversations",
            None,
            parameter="conversation_id",
        )
        assert target is None
        assert "conversation_id is required" in error


class TestEditToolsArgumentValidation:
    @pytest.fixture
    def valid_config(self):
        return {
            "configurable": {
                "user_id": "owner_123",
                "chat_session_id": "session_123",
                "user_timezone": "UTC",
            }
        }

    def test_rename_person_rejects_empty_name(self, valid_config):
        res = edit_tools.rename_person.func("person_1", "", config=valid_config)
        assert "invalid_name" in res
        assert "1 to 100 characters" in res

    def test_rename_person_rejects_none_name(self, valid_config):
        res = edit_tools.rename_person.func("person_1", None, config=valid_config)
        assert "invalid_name" in res
        assert "1 to 100 characters" in res

    def test_rename_person_rejects_whitespace_name(self, valid_config):
        res = edit_tools.rename_person.func("person_1", "   \t\n  ", config=valid_config)
        assert "invalid_name" in res
        assert "1 to 100 characters" in res

    def test_rename_person_rejects_too_long_name(self, valid_config):
        res = edit_tools.rename_person.func("person_1", "a" * 101, config=valid_config)
        assert "invalid_name" in res
        assert "1 to 100 characters" in res

    def test_tools_raise_permission_error_on_none_configurable(self):
        bad_config = {"configurable": None}
        with pytest.raises(PermissionError):
            edit_tools.update_conversation.func("c1", "correction", "title", config=bad_config)
        with pytest.raises(PermissionError):
            edit_tools.rename_person.func("p1", "New Name", config=bad_config)
        with pytest.raises(PermissionError):
            edit_tools.edit_memory.func("m1", "New Content", config=bad_config)
        with pytest.raises(PermissionError):
            edit_tools.delete_memory.func("m1", config=bad_config)
