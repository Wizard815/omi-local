"""Pure rules of the end-of-day review (utils/day_review.py)."""

from datetime import datetime, timedelta, timezone

from utils import day_review as dr

SUMMARY = (
    'You started the day with coffee at Blue Bottle with Sam.\n\n'
    'At noon you met Dana about the Q4 budget and agreed to cut travel.\n\n'
    'In the evening you called your mom about the Tuesday dinner.'
)


def test_redactions_mask_everywhere_case_insensitively():
    corrections = [
        {'type': 'redaction', 'quote': 'blue bottle'},
        {'type': 'paragraph', 'text': 'Remember Blue Bottle closes early.', 'applied_at': None},
    ]
    final = dr.compose_final_text(SUMMARY, corrections)
    assert 'Blue Bottle' not in final and 'blue bottle' not in final.lower()
    assert final.count(dr.REDACTED) == 2  # in the summary and in the user's own paragraph


def test_highlight_comment_lands_after_the_selected_occurrence():
    text = 'Sam said hi. Later Sam left.'
    second = text.index('Sam', 5)
    final = dr.compose_final_text(text, [{'type': 'highlight', 'quote': 'Sam', 'start': second, 'text': 'it was Alex'}])
    assert final == 'Sam said hi. Later Sam [correction: it was Alex] left.'


def test_paragraphs_insert_at_their_anchor_or_the_end_and_applied_ones_are_skipped():
    corrections = [
        {'type': 'paragraph', 'text': 'Also: gym at 7.', 'after_paragraph': 0},
        {'type': 'paragraph', 'text': 'Went to bed early.'},
        {'type': 'paragraph', 'text': 'Already folded in.', 'applied_at': datetime(2026, 10, 11)},
        {'type': 'highlight', 'quote': 'Dana', 'text': 'Dana is my manager', 'applied_at': datetime(2026, 10, 11)},
    ]
    paragraphs = dr.split_paragraphs(dr.compose_final_text(SUMMARY, corrections))
    assert paragraphs[1] == 'Also: gym at 7.'
    assert paragraphs[-1] == 'Went to bed early.'
    assert 'Already folded in.' not in paragraphs
    assert '[correction:' not in ' '.join(paragraphs)


def _item(hour, title, overview, transcript):
    start = datetime(2026, 10, 10, hour, tzinfo=timezone.utc)
    return dr.MaterialItem(start, start + timedelta(minutes=30), title, overview, ['Follow up'], transcript)


def test_material_keeps_every_conversation_within_budget_and_masks_redactions():
    long_meeting = _item(9, 'Budget', 'Q4 budget with Dana.', 'Dana: ' + 'numbers ' * 5000)
    lunch = _item(12, 'Lunch', 'Lunch with Sam at Blue Bottle.', 'Sam: the secret is out')
    material = dr.render_material([lunch, long_meeting], redactions=['the secret'], max_chars=3000)
    assert material.index('Budget') < material.index('Lunch')  # chronological
    assert 'Lunch with Sam' in material and 'Sam: [redacted] is out' in material  # short one still included
    assert len(material) <= 3000 + 2000


def test_material_puts_undated_conversations_after_dated_ones():
    undated = [dr.MaterialItem(None, None, f'Undated {n}', 'No timestamps.', [], '') for n in (1, 2)]
    material = dr.render_material([undated[0], _item(15, 'Call', 'Called mom.', ''), undated[1]])
    assert material.index('Call') < material.index('Undated 1') < material.index('Undated 2')


def test_prompt_carries_corrections_as_ground_truth_and_never_leaks_redactions():
    corrections = [
        {'type': 'paragraph', 'text': 'Dinner moved to Wednesday.'},
        {'type': 'highlight', 'quote': 'Tuesday dinner', 'text': 'it is on Wednesday'},
        {'type': 'redaction', 'quote': 'Blue Bottle'},
    ]
    prompt = dr.build_prompt(
        date_label='Saturday', material='coffee at Blue Bottle', corrections=corrections, previous_summary=SUMMARY
    )
    assert 'Include this (written by the user): Dinner moved to Wednesday.' in prompt
    assert 'About "Tuesday dinner": it is on Wednesday' in prompt
    assert 'Never mention' in prompt and '- "Blue Bottle"' in prompt
    # Only the never-mention list may name it; draft and material are masked.
    assert prompt.count('Blue Bottle') == 1


def test_output_parsing_strips_markdown_and_enforces_redactions():
    raw = '```\nTITLE: **Busy Saturday**\n\n# Morning\n- Coffee at Blue Bottle with Sam.\n\nNoon budget talk.\n```'
    title, body = dr.parse_summary_output(raw, redactions=['Blue Bottle'])
    assert title == 'Busy Saturday'
    assert body == 'Morning\nCoffee at [redacted] with Sam.\n\nNoon budget talk.'
    assert dr.parse_summary_output('Just one paragraph. More.')[0] == 'Just one paragraph'
