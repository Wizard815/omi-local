"""config/conversation_hold.py: on a self-hosted stack a finished clip waits for
the day review with no model call.

Runs the real process_conversation coordinator with the offline fakes of
test_process_conversation_free_tier_branch and pins down:

  * capture end / client stop / sync upload store the transcript with a
    model-free title, the noise rule that let it through, and no LLM call,
  * rule-detected noise (filler only) is hidden straight away, still with no
    LLM call,
  * the explicit actions (review keep = FIRST_OPEN, reprocess, merge) run the
    full pipeline and clear the held marker,
  * the hold is off outside a self-hosted stack and can be switched off there.
"""

from __future__ import annotations

import os

import pytest

from models.conversation import CreateConversation, Conversation
from models.conversation_enums import ConversationSource, ConversationStatus
from models.structured import Structured
from models.transcript_segment import TranscriptSegment
from testing.import_isolation import load_module_fresh, stub_modules

from config import conversation_hold
from tests.unit.test_process_conversation_free_tier_branch import (
    _BACKEND,
    _build_fakes,
    _capture_all_persists,
    _omi_create,
    _spy_managed_effects,
    _stub_completed_for_normal_path,
)


@pytest.fixture(scope='module')
def pc():
    with stub_modules(_build_fakes()):
        yield load_module_fresh(
            'utils.conversations.process_conversation',
            os.path.join(str(_BACKEND), 'utils', 'conversations', 'process_conversation.py'),
        )


@pytest.fixture
def self_hosted(monkeypatch, pc):
    monkeypatch.setenv('OMI_SELF_HOSTED', '1')
    monkeypatch.delenv('OMI_CONVERSATION_HOLD', raising=False)
    monkeypatch.setattr(pc.funded_processing, 'enabled', lambda uid, lane=None: False)
    monkeypatch.setattr(pc, '_calendar_overlap_retains_conversation', lambda *a, **k: False)


def _filler_create() -> CreateConversation:
    create = _omi_create()
    create.transcript_segments = [
        TranscriptSegment(text='yeah yeah um okay', speaker='SPEAKER_00', is_user=True, start=0.0, end=1.0)
    ]
    return create


def _held_row(conv_id: str = 'held-1') -> Conversation:
    create = _omi_create()
    return Conversation(
        id=conv_id,
        created_at=create.started_at,
        started_at=create.started_at,
        finished_at=create.finished_at,
        transcript_segments=create.transcript_segments,
        source=ConversationSource.omi,
        language='en',
        structured=Structured(title='Hello from the phone capture'),
        status=ConversationStatus.completed,
        processing_deferred=True,
    )


@pytest.mark.parametrize('trigger', ['CAPTURE_END', 'CLIENT_FINALIZE', 'SYNC_UPDATE'])
def test_a_finished_clip_waits_for_the_review_without_a_model_call(monkeypatch, pc, self_hosted, trigger):
    spies = _spy_managed_effects(monkeypatch, pc)
    payloads = _capture_all_persists(monkeypatch, pc)

    result = pc.process_conversation('owner', 'en', _omi_create(), trigger=getattr(pc.ProcessingTrigger, trigger))

    spies['get_structured'].assert_not_called()
    spies['extract_memories_inner'].assert_not_called()
    assert result.status == ConversationStatus.completed
    assert result.processing_deferred is True and result.discarded is False
    assert result.structured.title == 'Hello from the phone capture'
    stored = payloads[-1]
    assert stored['processing_deferred'] is True
    assert stored[conversation_hold.HOLD_RULE_FIELD] == 'model_withheld'
    assert stored[pc.TERMINAL_NO_DERIVED_EFFECTS_FIELD] is True


def test_rule_detected_noise_is_hidden_without_a_model_call(monkeypatch, pc, self_hosted):
    spies = _spy_managed_effects(monkeypatch, pc)
    payloads = _capture_all_persists(monkeypatch, pc)

    result = pc.process_conversation('owner', 'en', _filler_create())

    spies['get_structured'].assert_not_called()
    assert result.discarded is True and result.processing_deferred is False
    assert payloads[-1]['discarded'] is True
    assert payloads[-1][conversation_hold.HOLD_RULE_FIELD] == 'filler_only'


@pytest.mark.parametrize('trigger', ['FIRST_OPEN', 'USER_REPROCESS', 'MERGE'])
def test_an_explicit_action_on_a_held_clip_runs_the_pipeline(monkeypatch, pc, self_hosted, trigger):
    spies = _spy_managed_effects(monkeypatch, pc)
    _stub_completed_for_normal_path(monkeypatch, pc, conv_id='held-1')

    result = pc.process_conversation(
        'owner',
        'en',
        _held_row(),
        trigger=getattr(pc.ProcessingTrigger, trigger),
        persistence_observer=lambda _owned: None,
        defer_derived_effects=True,
        derived_effects_observer=lambda _runner: None,
    )

    spies['get_structured'].assert_called_once()
    assert not result.processing_deferred


def test_the_hold_is_off_unless_self_hosted_or_switched_on(monkeypatch, pc):
    monkeypatch.delenv('OMI_SELF_HOSTED', raising=False)
    monkeypatch.delenv('OMI_CONVERSATION_HOLD', raising=False)
    monkeypatch.setattr(pc.funded_processing, 'enabled', lambda uid, lane=None: False)
    spies = _spy_managed_effects(monkeypatch, pc)
    _stub_completed_for_normal_path(monkeypatch, pc)

    pc.process_conversation(
        'cloud-user',
        'en',
        _omi_create(),
        persistence_observer=lambda _owned: None,
        defer_derived_effects=True,
        derived_effects_observer=lambda _runner: None,
    )
    spies['get_structured'].assert_called_once()

    assert conversation_hold.hold_enabled({}) is False
    assert conversation_hold.hold_enabled({'OMI_CONVERSATION_HOLD': 'on'}) is True
    assert conversation_hold.hold_enabled({'OMI_SELF_HOSTED': '1'}) is True
    assert conversation_hold.hold_enabled({'OMI_SELF_HOSTED': '1', 'OMI_CONVERSATION_HOLD': 'off'}) is False
