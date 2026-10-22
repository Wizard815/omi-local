"""End-of-day review side effects (utils/day_review_service.py).

Seams: conversations_db / day_reviews_db / notification_db / lifecycle /
get_llm are replaced by in-memory fakes; the service's ordering and data rules
run for real.
"""

import contextlib
from datetime import datetime, timezone

import pytest

from utils import day_review_service as service

DAY = '2026-10-10'


def _conv(cid, hour, title, *, starred=False, status='completed', discarded=False, **extra):
    start = datetime(2026, 10, 10, hour, tzinfo=timezone.utc)
    return {
        'id': cid,
        'created_at': start,
        'started_at': start,
        'finished_at': start.replace(minute=30),
        'structured': {'title': title, 'overview': f'{title} overview', 'emoji': '💬'},
        'transcript_segments': [
            {'text': f'{title} talk', 'speaker': 'SPEAKER_00', 'speaker_id': 0, 'is_user': False, 'start': 0, 'end': 1}
        ],
        'status': status,
        'discarded': discarded,
        'starred': starred,
        **extra,
    }


class _Llm:
    def __init__(self):
        self.prompts = []
        self.fail = False
        self.reply = 'TITLE: A full Saturday\n\nYou had lunch.\n\nThen a budget meeting.'

    def invoke(self, prompt):
        self.prompts.append(prompt)
        if self.fail:
            raise TimeoutError('model cold start')
        return type('R', (), {'content': self.reply})()


@pytest.fixture
def world(monkeypatch):
    convs = {
        'lunch': _conv('lunch', 12, 'Lunch'),
        'budget': _conv('budget', 14, 'Budget', starred=True),
        'call': _conv('call', 19, 'Call with mom'),
        'live': _conv('live', 20, 'Still recording', status='in_progress'),
        'auto-discarded': _conv('auto-discarded', 21, 'Noise', discarded=True),
        # Held by the omi-local hold: transcript, cheap title, no overview, a noise rule.
        'held-a': _conv(
            'held-a', 9, 'Quick coffee chat', processing_deferred=True, processing_hold_rule='model_withheld'
        ),
        'held-b': _conv(
            'held-b', 10, 'Coffee continued', processing_deferred=True, processing_hold_rule='model_withheld'
        ),
        'held-c': _conv('held-c', 16, 'Hallway noise', processing_deferred=True, processing_hold_rule='model_withheld'),
    }
    for cid in ('held-a', 'held-b', 'held-c'):
        convs[cid]['structured']['overview'] = ''
    reviews = {}
    llm = _Llm()
    submitted = []

    def get_conversations(uid, limit=100, include_discarded=False, start_date=None, end_date=None, **_):
        rows = [dict(c) for c in convs.values() if include_discarded or not c['discarded']]
        return [r for r in rows if start_date <= r['started_at'] <= end_date]

    def update_conversation(uid, cid, data):
        convs[cid].update(data)
        return True

    monkeypatch.setattr(service.conversations_db, 'get_conversations', get_conversations)
    monkeypatch.setattr(
        service.conversations_db,
        'get_conversations_by_id',
        lambda uid, ids, include_discarded=False: [dict(convs[i]) for i in ids if i in convs],
    )
    monkeypatch.setattr(service.conversations_db, 'get_conversation', lambda uid, cid: convs.get(cid))
    monkeypatch.setattr(
        service.conversations_db, 'set_conversation_starred', lambda uid, cid, v: convs[cid].update(starred=v)
    )
    monkeypatch.setattr(service.conversations_db, 'update_conversation', update_conversation)
    monkeypatch.setattr(service.lifecycle, 'discard', lambda uid, cid: convs[cid].update(discarded=True))
    monkeypatch.setattr(service.lifecycle, 'begin_merge', lambda uid, cid: convs[cid].update(status='merging'))
    monkeypatch.setattr(service, '_submit', lambda fn, *args: submitted.append((fn.__name__, *args)))
    monkeypatch.setenv('OMI_SELF_HOSTED', '1')
    monkeypatch.setattr(service.lifecycle, 'restore_discarded', lambda uid, cid: convs[cid].update(discarded=False))
    monkeypatch.setattr(
        service.day_reviews_db, 'get_day_review', lambda uid, d: dict(reviews[d]) if d in reviews else None
    )
    monkeypatch.setattr(
        service.day_reviews_db, 'set_day_review', lambda uid, d, data: reviews.__setitem__(d, {**data, 'date': d})
    )
    monkeypatch.setattr(service.day_reviews_db, 'delete_day_review', lambda uid, d: reviews.pop(d, None))
    monkeypatch.setattr(service.day_reviews_db, 'list_day_reviews', lambda uid, limit=30: list(reviews.values()))
    monkeypatch.setattr(service.notification_db, 'get_user_time_zone', lambda uid: 'UTC')
    monkeypatch.setattr(service, 'get_llm', lambda feature, request_timeout=None: llm)
    monkeypatch.setattr(service, 'track_usage', lambda uid, feature: contextlib.nullcontext())
    return convs, reviews, llm, submitted


def test_the_day_lists_completed_conversations_and_counts_unfinished_ones(world):
    day = service.get_day('u1', DAY)
    assert [c['id'] for c in day['conversations']] == ['held-a', 'held-b', 'lunch', 'budget', 'held-c', 'call']
    assert day['processing_count'] == 1 and day['review'] is None
    assert day['dropped_auto_count'] == 1 and day['hold_enabled'] is True
    held = next(c for c in day['conversations'] if c['id'] == 'held-a')
    assert held['held'] and held['hold_rule'] == 'model_withheld'
    assert held['transcript_snippet'] == 'Quick coffee chat talk' and held['word_count'] == 4
    assert not next(c for c in day['conversations'] if c['id'] == 'lunch')['held']


def test_finalize_keeps_starred_and_rolls_the_rest_into_one_summary(world):
    convs, reviews, llm, submitted = world

    review = service.finalize('u1', DAY, ['budget'])

    assert review['title'] == 'A full Saturday'
    assert review['rolled_up_conversation_ids'] == ['held-a', 'held-b', 'lunch', 'held-c', 'call']
    assert convs['budget']['starred'] and not convs['budget']['discarded']
    for cid in ('lunch', 'call', 'held-a'):
        assert convs[cid]['discarded'] and convs[cid]['day_review_date'] == DAY
    assert 'Lunch overview' in llm.prompts[0] and 'Budget overview' not in llm.prompts[0]
    assert 'Quick coffee chat talk' in llm.prompts[0], 'a held clip is summarized from its transcript'
    assert submitted == [], 'nothing kept was held, so no enrichment runs'
    # The reviewed day now shows the rolled-up conversations as such.
    assert {c['id']: c['rolled_up'] for c in service.get_day('u1', DAY)['conversations']} == {
        'held-a': True,
        'held-b': True,
        'lunch': True,
        'budget': False,
        'held-c': True,
        'call': True,
    }
    with pytest.raises(service.DayReviewError) as again:
        service.finalize('u1', DAY, [])
    assert again.value.status_code == 409


def test_finalize_enriches_kept_held_clips_merges_groups_and_drops_noise(world):
    convs, reviews, llm, submitted = world

    review = service.finalize('u1', DAY, ['held-c', 'budget'], [['held-a', 'held-b']], ['call'])

    assert review['kept_conversation_ids'] == ['budget', 'held-c']
    assert review['merged_groups'] == [{'conversation_ids': ['held-a', 'held-b']}]
    assert review['dropped_conversation_ids'] == ['call']
    assert review['rolled_up_conversation_ids'] == ['lunch']
    # Only the kept held clip is enriched; the merge runs the pipeline on its result.
    assert submitted == [('_enrich_held', 'u1', 'held-c'), ('_merge_group', 'u1', ['held-a', 'held-b'])]
    assert convs['held-c']['starred'] and not convs['held-c']['discarded']
    assert convs['held-a']['status'] == 'merging' and convs['held-b']['status'] == 'merging'
    assert convs['call']['discarded'] and convs['call']['day_review_date'] == DAY
    assert 'Call with mom' not in llm.prompts[0] and 'Lunch overview' in llm.prompts[0]
    rows = {c['id']: c for c in service.get_day('u1', DAY)['conversations']}
    assert rows['call']['dropped'] and not rows['call']['rolled_up']
    assert rows['lunch']['rolled_up'] and not rows['lunch']['dropped']


def test_a_clip_can_only_be_sorted_once(world):
    with pytest.raises(service.DayReviewError) as exc:
        service.finalize('u1', DAY, ['lunch'], [['lunch', 'call']])
    assert exc.value.status_code == 422
    with pytest.raises(service.DayReviewError) as exc:
        service.finalize('u1', DAY, [], [['lunch']])
    assert exc.value.status_code == 422


def test_a_model_failure_changes_nothing(world):
    convs, reviews, llm, submitted = world
    llm.fail = True
    with pytest.raises(service.DayReviewError) as exc:
        service.finalize('u1', DAY, [])
    assert exc.value.status_code == 502
    assert reviews == {}
    assert not any(c.get('day_review_date') for c in convs.values())
    assert not convs['lunch']['discarded']
    assert submitted == []


def test_keep_ids_must_belong_to_the_day(world):
    with pytest.raises(service.DayReviewError) as exc:
        service.finalize('u1', DAY, ['live'])  # still recording → not sortable yet
    assert exc.value.status_code == 422


def test_corrections_then_regenerate_applies_them_and_keeps_redactions(world):
    convs, reviews, llm, _ = world
    review = service.finalize('u1', DAY, [])
    summary = review['summary_text']
    start = summary.index('lunch')

    service.add_correction(
        'u1', DAY, {'type': 'highlight', 'quote': 'lunch', 'start': start, 'end': start + 5, 'text': 'it was brunch'}
    )
    service.add_correction('u1', DAY, {'type': 'redaction', 'quote': 'budget', 'start': 999, 'end': 1005})
    review = service.add_correction('u1', DAY, {'type': 'paragraph', 'text': 'I also went running.'})

    assert 'lunch [correction: it was brunch]' in review['final_text']
    assert 'budget' not in review['final_text'].lower()
    assert review['corrections'][1]['start'] is None  # offsets that did not match the text are dropped
    assert review['final_text'].endswith('I also went running.')

    llm.reply = 'TITLE: Brunch day\n\nYou had brunch, then a budget meeting.'
    review = service.regenerate('u1', DAY)

    prompt = llm.prompts[-1]
    assert 'About "lunch": it was brunch' in prompt and 'I also went running.' in prompt
    assert 'budget' not in review['summary_text'].lower()  # model said it anyway; masked
    applied = {c['type']: c['applied_at'] for c in review['corrections']}
    assert applied['highlight'] and applied['paragraph'] and applied['redaction'] is None
    assert review['generation_count'] == 2
    assert 'I also went running.' not in review['final_text']  # folded in, not re-appended


def test_reopen_restores_rolled_and_dropped_but_not_merges(world):
    convs, reviews, _, _ = world
    service.finalize('u1', DAY, ['budget'], [['held-a', 'held-b']], ['call'])
    assert service.reopen('u1', DAY) == (3, 1)  # lunch + held-c rolled, call dropped; one merge stays
    assert reviews == {}
    for cid in ('lunch', 'held-c', 'call'):
        assert not convs[cid]['discarded'] and convs[cid]['day_review_date'] is None


def test_pending_dates_skip_reviewed_days(world, monkeypatch):
    monkeypatch.setattr(service, 'local_today', lambda tz: datetime(2026, 10, 11).date())
    assert service.pending_dates('u1', days=3) == {'today': '2026-10-11', 'pending_dates': [DAY]}
    service.finalize('u1', DAY, [])
    assert service.pending_dates('u1', days=3)['pending_dates'] == []
