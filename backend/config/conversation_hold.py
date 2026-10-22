"""Hold finished conversations until the end-of-day review (omi-local).

With the hold on, a conversation that ends on its own (capture silence, the
client's stop button, an offline-sync upload) is stored with its transcript, a
title from its first words and the rule-based noise check only: no model call.
The day review (utils/day_review_service.py) is where the work happens, once,
for the clips the user kept or merged; the rest become one day summary.

Explicit actions on a row (reprocess, merge, opening a deferred row) always run
the full pipeline.

OMI_CONVERSATION_HOLD: on | off. Default: on for a self-hosted stack
(OMI_SELF_HOSTED=1), off otherwise.
"""

import os
from typing import Mapping, Optional

from utils.conversations.processing_trigger import ProcessingTrigger
from utils.self_hosting import is_self_hosted

HOLD_TRIGGERS = frozenset(
    {ProcessingTrigger.CAPTURE_END, ProcessingTrigger.CLIENT_FINALIZE, ProcessingTrigger.SYNC_UPDATE}
)
# Stored on a held row: the rule that let it through (see relevance_rules).
HOLD_RULE_FIELD = 'processing_hold_rule'


def hold_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    source = os.environ if environ is None else environ
    raw = source.get('OMI_CONVERSATION_HOLD', '').strip().lower()
    if raw in {'1', 'on', 'true', 'yes'}:
        return True
    if raw in {'0', 'off', 'false', 'no'}:
        return False
    return is_self_hosted(source)


def holds(trigger: ProcessingTrigger, environ: Optional[Mapping[str, str]] = None) -> bool:
    return trigger in HOLD_TRIGGERS and hold_enabled(environ)
