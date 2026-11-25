"""A model that copies the tool's "Conversation #N" heading into its marker still cites.

The conversation tool renders each record as "Conversation #1", "Conversation #2"... and
models write "[Conversation #1]" instead of "[1]". The marker pattern did not match it:
the answer kept the raw marker and the message got no citation cards (memories_id empty).
"""

from utils.chat_citations import resolve_conversation_citations
from utils.messaging.adapters.rendering import strip_citations

CARDS = [{'id': 'insurance'}, {'id': 'plumber'}, {'id': 'school'}]


def test_conversation_heading_markers_resolve_to_cards_and_are_stripped():
    text = (
        'Your car insurance renewal is **$612 this year** [Conversation #1]. '
        'Gary comes Thursday [Conversation #2, #1]. Field trip money [conversation 3].'
    )
    stripped, cited = resolve_conversation_citations(text, CARDS)
    assert stripped == 'Your car insurance renewal is **$612 this year**. Gary comes Thursday. Field trip money.'
    assert [c['id'] for c in cited] == ['insurance', 'plumber', 'school']


def test_plain_markers_and_links_are_unchanged():
    stripped, cited = resolve_conversation_citations('Renewal is $612[1][2].', CARDS)
    assert stripped == 'Renewal is $612.' and [c['id'] for c in cited] == ['insurance', 'plumber']
    keep = 'See [Conversation](https://example.com) and [Conversation notes].'
    assert strip_citations(keep) == keep
    assert resolve_conversation_citations('[Conversation #9]', CARDS) == ('', [])
