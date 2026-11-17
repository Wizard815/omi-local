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
    # the approval is undone, the summary stays as a draft (off the calendar until approved again)
    assert reviews[DAY]['status'] == 'draft' and reviews[DAY]['summary_text']
    # reopening the draft discards it
    assert service.reopen('u1', DAY) == (0, 0) and reviews == {}
    for cid in ('lunch', 'held-c', 'call'):
        assert not convs[cid]['discarded'] and convs[cid]['day_review_date'] is None


def test_pending_dates_skip_reviewed_days(world, monkeypatch):
    monkeypatch.setattr(service, 'local_today', lambda tz: datetime(2026, 10, 11).date())
    assert service.pending_dates('u1', days=3) == {'today': '2026-10-11', 'pending_dates': [DAY], 'draft_dates': []}
    service.finalize('u1', DAY, [])
    assert service.pending_dates('u1', days=3)['pending_dates'] == []


# --- bundle, sections, favorite-out, auto tags ----------------------------------------------


@pytest.fixture
def bundled(world, monkeypatch):
    convs, reviews, llm, submitted = world
    tagged = {}

    def set_tags(uid, cid, tags):
        tagged[cid] = list(tags)
        convs[cid]['tags'] = list(tags)

    monkeypatch.setattr(service.conversations_db, 'set_conversation_tags', set_tags)
    monkeypatch.setattr(service.day_bundle_users_db, 'mark_active', lambda uid: None)
    llm.reply = (
        'TITLE: Coffee and calls\n\n'
        'SECTION c1+c2: Morning coffee\nYou talked shop over coffee.\nTAGS: coffee, work\n\n'
        'SECTION c3: Lunch\nYou had lunch.\nTAGS: lunch\n\n'
        'SECTION c4+c5: Afternoon\nHallway noise, then mom called.\nTAGS: family\n'
    )
    return convs, reviews, llm, submitted, tagged


def test_bundle_keeps_starred_and_sections_the_rest_with_tags(bundled):
    convs, reviews, llm, submitted, tagged = bundled
    draft = service.bundle('u1', DAY)
    # a draft: written, but nothing sorted, tagged or discarded until the user approves it
    assert draft['draft'] and draft['status'] == 'finalized' and draft['kept_conversation_ids'] == ['budget']
    assert tagged == {} and not any(convs[c]['discarded'] for c in draft['rolled_up_conversation_ids'])
    review = service.approve('u1', DAY)
    assert review['status'] == 'finalized' and len(llm.prompts) == 1  # approving reused the draft's text
    assert review['kept_conversation_ids'] == ['budget']
    assert review['rolled_up_conversation_ids'] == ['held-a', 'held-b', 'lunch', 'held-c', 'call']
    assert [s['conversation_ids'] for s in review['sections']] == [['held-a', 'held-b'], ['lunch'], ['held-c', 'call']]
    assert [s['title'] for s in review['sections']] == ['Morning coffee', 'Lunch', 'Afternoon']
    assert review['tags'] == ['coffee', 'family', 'lunch', 'work']
    assert review['final_text'] == 'You talked shop over coffee.\n\nYou had lunch.\n\nHallway noise, then mom called.'
    # the material labelled every rolled conversation in day order
    assert '[c1] 09:00' in llm.prompts[0] and '[c5] 19:00' in llm.prompts[0] and 'Budget' not in llm.prompts[0]
    assert tagged == {
        'held-a': ['coffee', 'work'],
        'held-b': ['coffee', 'work'],
        'lunch': ['lunch'],
        'held-c': ['family'],
        'call': ['family'],
    }
    assert all(convs[c]['discarded'] for c in review['rolled_up_conversation_ids'])
    assert not convs['budget']['discarded'] and convs['budget']['starred']


def test_bundle_refuses_a_future_day_and_a_reviewed_day(bundled):
    with pytest.raises(service.DayReviewError) as e:
        service.bundle('u1', '2099-01-01')
    assert e.value.status_code == 422
    service.bundle('u1', DAY)
    with pytest.raises(service.DayReviewError) as e:
        service.bundle('u1', DAY)
    assert e.value.status_code == 409


def test_favorite_a_section_pulls_its_conversations_back_out_and_unfavorite_folds_them_in(bundled):
    convs, reviews, llm, submitted, tagged = bundled
    service.bundle('u1', DAY)
    service.approve('u1', DAY)
    submitted.clear()
    review = service.favorite_section('u1', DAY, 's1', True)
    assert review['sections'][0]['favorited'] is True
    assert review['kept_conversation_ids'] == ['budget', 'held-a', 'held-b']
    assert review['rolled_up_conversation_ids'] == ['lunch', 'held-c', 'call']
    for cid in ('held-a', 'held-b'):
        assert not convs[cid]['discarded'] and convs[cid]['starred']
    # the held clips get the full pipeline, the review text is unchanged
    assert submitted == [('_enrich_held', 'u1', 'held-a'), ('_enrich_held', 'u1', 'held-b')]
    assert review['final_text'].startswith('You talked shop over coffee.')
    assert service.favorite_section('u1', DAY, 's1', True)['sections'][0]['favorited'] is True  # idempotent

    review = service.favorite_section('u1', DAY, 's1', False)
    assert review['sections'][0]['favorited'] is False
    assert review['kept_conversation_ids'] == ['budget']
    assert review['rolled_up_conversation_ids'] == ['lunch', 'held-c', 'call', 'held-a', 'held-b']
    assert convs['held-a']['discarded'] and not convs['held-a']['starred']
    with pytest.raises(service.DayReviewError) as e:
        service.favorite_section('u1', DAY, 'nope', True)
    assert e.value.status_code == 404


def test_regenerate_keeps_a_favorited_section_favorited_and_retags(bundled):
    convs, reviews, llm, submitted, tagged = bundled
    service.bundle('u1', DAY)
    service.approve('u1', DAY)
    service.favorite_section('u1', DAY, 's2', True)
    llm.reply = 'TITLE: Again\n\nSECTION c1+c2+c3: Everything\nAll of it.\nTAGS: all\n\nSECTION c4+c5: Later\nLater.\n'
    service.add_correction('u1', DAY, {'type': 'paragraph', 'text': 'Also: the dentist called.'})
    review = service.regenerate('u1', DAY)
    assert [s['favorited'] for s in review['sections']] == [True, False]
    assert tagged['lunch'] == ['lunch', 'all'] and tagged['held-a'] == ['coffee', 'work', 'all']


# --- drafts: written overnight, approved by the user ---------------------------------------------


def test_a_draft_touches_nothing_and_counts_as_still_waiting(bundled, monkeypatch):
    convs, reviews, llm, submitted, tagged = bundled
    monkeypatch.setattr(service, 'local_today', lambda tz: datetime(2026, 10, 11).date())
    draft = service.prepare_draft('u1', DAY)
    assert draft['draft'] and draft['final_text']
    assert not any(c['discarded'] for c in convs.values() if c['id'] != 'auto-discarded')
    assert submitted == [] and tagged == {}
    assert service.pending_dates('u1', days=3) == {
        'today': '2026-10-11',
        'pending_dates': [DAY],
        'draft_dates': [DAY],
    }
    # favoriting a section of a draft only plans it: starred, nothing discarded or enriched
    review = service.favorite_section('u1', DAY, 's1', True)
    assert review['draft'] and convs['held-a']['starred'] and submitted == []
    assert review['kept_conversation_ids'] == ['budget', 'held-a', 'held-b']
    # approving uses the draft as it stands: no second model call
    approved = service.approve('u1', DAY)
    assert not approved['draft'] and approved['approved_at'] and len(llm.prompts) == 1
    assert approved['kept_conversation_ids'] == ['held-a', 'held-b', 'budget']  # in day order
    assert service.pending_dates('u1', days=3)['pending_dates'] == []
    with pytest.raises(service.DayReviewError) as e:
        service.approve('u1', DAY)
    assert e.value.status_code == 409


def test_sorting_a_draft_differently_rewrites_it_with_its_corrections_on_approval(bundled):
    convs, reviews, llm, submitted, tagged = bundled
    service.prepare_draft('u1', DAY)
    service.add_correction('u1', DAY, {'type': 'paragraph', 'text': 'We also walked the dog.'})
    llm.reply = 'TITLE: Shorter day\n\nSECTION c1: Lunch\nLunch only.\n'
    review = service.finalize('u1', DAY, ['budget', 'held-a', 'held-b', 'held-c'], (), ['call'])
    assert review['status'] == 'finalized' and len(llm.prompts) == 2
    assert 'We also walked the dog.' in llm.prompts[-1]  # the draft's corrections are ground truth
    assert review['corrections'][0]['applied_at'] is not None
    assert review['rolled_up_conversation_ids'] == ['lunch'] and convs['call']['discarded']


def test_an_everything_kept_day_keeps_its_paragraph_through_approve_and_reopen(world):
    """All starred: no summary is written, the user's paragraph is the day's text. Approving
    keeps it, and reopening turns it back into a draft instead of losing it."""
    convs, reviews, llm, submitted = world
    for cid in ('lunch', 'call', 'held-a', 'held-b', 'held-c'):
        convs[cid]['starred'] = True
    service.prepare_draft('u1', DAY)
    service.add_correction('u1', DAY, {'type': 'paragraph', 'text': 'A quiet day at home.'})
    approved = service.approve('u1', DAY)
    assert approved['final_text'] == 'A quiet day at home.' and llm.prompts == []
    service.reopen('u1', DAY)
    assert reviews[DAY]['status'] == 'draft' and reviews[DAY]['corrections']
    assert service.approve('u1', DAY)['final_text'] == 'A quiet day at home.'


def test_a_released_app_never_sees_a_draft(bundled, monkeypatch):
    """Released apps decode status 'finalized' only: a draft reaches them as an unreviewed day
    (they offer sorting, and their finalize approves it), never as a finished review."""
    convs, reviews, llm, submitted, tagged = bundled
    monkeypatch.setattr(service.day_bundle_users_db, 'mark_active', lambda uid: None)
    service.prepare_draft('u1', DAY)
    assert service.get_day('u1', DAY, include_drafts=False)['review'] is None
    assert service.list_reviews('u1', include_drafts=False) == []
    shown = service.get_day('u1', DAY)['review']
    assert shown['draft'] is True and shown['status'] == 'finalized'
    assert reviews[DAY]['status'] == 'draft'  # stored as a draft
    review = service.finalize('u1', DAY, ['budget'])  # the old app's sort-and-summarize
    assert review['draft'] is False and len(llm.prompts) == 1  # same split as the draft: text reused


# --- editing the text by hand (PUT /day-reviews/{date}/text) -------------------------------------


@pytest.fixture
def marks(monkeypatch):
    marked = []
    monkeypatch.setattr(service.caldav_sync, 'mark_dirty', lambda uid: marked.append(uid))
    return marked


def test_editing_a_draft_keeps_it_a_draft_and_pushes_nothing(bundled, marks):
    convs, reviews, llm, submitted, tagged = bundled
    service.prepare_draft('u1', DAY)
    marks.clear()
    review = service.edit_text(
        'u1',
        DAY,
        title='  Coffee, lunch and mom  ',
        sections=[{'id': 's2', 'title': 'Lunch with Dana', 'text': 'Lunch with Dana.\n\n\n\nShe got the job.'}],
    )
    assert review['draft'] is True and reviews[DAY]['status'] == 'draft'
    assert review['title'] == 'Coffee, lunch and mom'
    assert review['sections'][1]['title'] == 'Lunch with Dana'
    assert review['sections'][1]['text'] == 'Lunch with Dana.\n\nShe got the job.'
    # the body is the sections' text, so corrections keep anchoring on what the user reads
    assert review['summary_text'] == (
        'You talked shop over coffee.\n\nLunch with Dana.\n\nShe got the job.\n\nHallway noise, then mom called.'
    )
    assert review['edited_by_user_at'] is not None
    assert 'generated_version' not in review  # stored only, never returned
    assert marks == []  # a draft is not on the calendar


def test_editing_an_approved_day_stays_approved_and_re_pushes_its_calendar_summary(bundled, marks):
    convs, reviews, llm, submitted, tagged = bundled
    service.prepare_draft('u1', DAY)
    approved = service.approve('u1', DAY)
    marks.clear()
    review = service.edit_text('u1', DAY, sections=[{'id': 's1', 'text': 'Coffee with Sam.'}])
    assert review['draft'] is False and reviews[DAY]['status'] == 'finalized'
    assert reviews[DAY]['approved_at'] == approved['approved_at']
    assert review['final_text'].startswith('Coffee with Sam.')
    assert marks == ['u1']
    # nothing changed: no write, no push
    marks.clear()
    service.edit_text('u1', DAY, sections=[{'id': 's1', 'text': 'Coffee with Sam.'}])
    assert marks == []


def test_edit_validates_sections_and_shape(bundled):
    service.prepare_draft('u1', DAY)
    with pytest.raises(service.DayReviewError) as e:
        service.edit_text('u1', DAY, sections=[{'id': 'nope', 'text': 'x'}])
    assert e.value.status_code == 422
    with pytest.raises(service.DayReviewError) as e:
        service.edit_text('u1', DAY, sections=[{'id': 's1', 'text': ' \n\n '}])
    assert e.value.status_code == 422
    with pytest.raises(service.DayReviewError) as e:
        service.edit_text('u1', DAY, summary_text='All of it')  # a sectioned summary is edited by section
    assert e.value.status_code == 422
    with pytest.raises(service.DayReviewError) as e:
        service.edit_text('u1', '2026-10-09', title='No review that day')
    assert e.value.status_code == 404


def test_a_summary_without_sections_is_edited_whole(world, marks):
    convs, reviews, llm, submitted = world
    reviews[DAY] = {'date': DAY, 'status': 'finalized', 'title': 'Old', 'summary_text': 'Old text.', 'sections': []}
    review = service.edit_text('u1', DAY, summary_text='New text.\n\nSecond paragraph.')
    assert review['summary_text'] == 'New text.\n\nSecond paragraph.' and marks == ['u1']


def test_undo_puts_the_models_text_back_and_clears_the_edit_mark(bundled):
    convs, reviews, llm, submitted, tagged = bundled
    draft = service.prepare_draft('u1', DAY)
    service.edit_text('u1', DAY, title='Mine', sections=[{'id': 's1', 'text': 'My words.'}])
    undone = service.edit_text(
        'u1',
        DAY,
        title=draft['title'],
        sections=[{'id': s['id'], 'title': s['title'], 'text': s['text']} for s in draft['sections']],
    )
    assert undone['summary_text'] == draft['summary_text']
    assert undone['edited_by_user_at'] is None and 'generated_version' not in reviews[DAY]


def test_rewrite_asks_before_replacing_hand_edits(bundled, marks):
    convs, reviews, llm, submitted, tagged = bundled
    service.prepare_draft('u1', DAY)
    service.approve('u1', DAY)
    service.edit_text('u1', DAY, sections=[{'id': 's1', 'text': 'My own words.'}])
    service.add_correction('u1', DAY, {'type': 'paragraph', 'text': 'Also: the dentist called.'})
    prompts = len(llm.prompts)
    with pytest.raises(service.DayReviewError) as e:
        service.regenerate('u1', DAY)
    assert e.value.status_code == 409 and e.value.message == service.EDITS_WOULD_BE_REPLACED
    assert len(llm.prompts) == prompts and reviews[DAY]['sections'][0]['text'] == 'My own words.'
    # confirmed: rewritten from the edited text (the earlier version the model rewrites), mark cleared
    marks.clear()
    review = service.regenerate('u1', DAY, replace_edits=True)
    assert 'My own words.' in llm.prompts[-1]
    assert review['edited_by_user_at'] is None and 'generated_version' not in reviews[DAY]
    assert marks == ['u1']  # the approved day's calendar summary follows the rewrite
    service.add_correction('u1', DAY, {'type': 'paragraph', 'text': 'One more.'})
    service.regenerate('u1', DAY)  # no edits since: no question


def test_approving_an_edited_draft_keeps_the_edits(bundled):
    convs, reviews, llm, submitted, tagged = bundled
    service.prepare_draft('u1', DAY)
    service.edit_text('u1', DAY, sections=[{'id': 's3', 'text': 'Mom called about Sunday.'}])
    approved = service.approve('u1', DAY)
    assert approved['final_text'].endswith('Mom called about Sunday.') and len(llm.prompts) == 1
    assert approved['edited_by_user_at'] is not None
    with pytest.raises(service.DayReviewError) as e:
        service.regenerate('u1', DAY)  # still the user's words: a rewrite still asks
    assert e.value.status_code == 409


def test_approving_an_edited_draft_whose_day_changed_asks_before_rewriting(bundled):
    convs, reviews, llm, submitted, tagged = bundled
    service.prepare_draft('u1', DAY)
    service.edit_text('u1', DAY, title='My day')
    convs['late'] = _conv('late', 22, 'Late chat')  # came in after the draft
    with pytest.raises(service.DayReviewError) as e:
        service.approve('u1', DAY)
    assert e.value.status_code == 409 and reviews[DAY]['status'] == 'draft'
    approved = service.approve('u1', DAY, replace_edits=True)
    assert 'late' in approved['rolled_up_conversation_ids'] and approved.get('edited_by_user_at') is None


# --- Approve all's Undo for a day: the draft comes back exactly -----------------------------------


def test_undo_of_an_approval_restores_the_draft_exactly_without_a_model_call(bundled, marks, monkeypatch):
    convs, reviews, llm, submitted, tagged = bundled
    service.prepare_draft('u1', DAY)
    service.add_correction('u1', DAY, {'type': 'paragraph', 'text': 'We also walked the dog.'})
    service.favorite_section('u1', DAY, 's1', True)  # planned: held-a and held-b stay as starred clips
    service.edit_text('u1', DAY, title='My Saturday')
    draft = dict(reviews[DAY])
    tags_before = {cid: list(c.get('tags') or []) for cid, c in convs.items()}
    starred_before = {cid: c['starred'] for cid, c in convs.items()}
    approved = service.approve('u1', DAY)
    assert not approved['draft'] and 'undo_approval' not in approved  # stored, never returned
    assert convs['call']['discarded'] and tagged
    prompts = len(llm.prompts)
    marks.clear()
    restored = service.undo_approval('u1', DAY)
    assert restored['draft'] is True and len(llm.prompts) == prompts
    assert reviews[DAY] == draft  # split, corrections, edits and text exactly as before
    assert not any(c['discarded'] for cid, c in convs.items() if cid != 'auto-discarded')
    assert {cid: c['starred'] for cid, c in convs.items()} == starred_before
    assert {cid: list(c.get('tags') or []) for cid, c in convs.items()} == tags_before
    assert marks == ['u1']  # its summary comes off the calendar
    assert service.undo_approval('u1', DAY)['draft'] is True  # undone already: unchanged
    # approve again works as before
    assert service.approve('u1', DAY)['title'] == 'My Saturday'


def test_undo_of_an_approval_is_only_for_a_fresh_approval_from_a_draft(bundled):
    convs, reviews, llm, submitted, tagged = bundled
    service.prepare_draft('u1', DAY)
    approved = service.approve('u1', DAY)
    later = approved['approved_at'] + service.UNDO_APPROVAL_WINDOW + service.timedelta(seconds=1)
    with pytest.raises(service.DayReviewError) as e:
        service.undo_approval('u1', DAY, now=later)
    assert e.value.status_code == 409 and reviews[DAY]['status'] == 'finalized'
    # a day sorted by hand (never a draft) has no draft to go back to
    reviews.clear()
    service.finalize('u1', DAY, ['budget'])
    with pytest.raises(service.DayReviewError):
        service.undo_approval('u1', DAY)
