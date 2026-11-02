"""utils/caldav/ical.py: what we write is what we read back, escaped and folded per RFC 5545."""

from datetime import datetime, timezone

from utils.caldav import ical


def test_todo_round_trips_with_escaping_and_folding():
    due = datetime(2026, 10, 30, 17, 0, tzinfo=timezone.utc)
    text = ical.render_todo(
        uid='omi-task-abc@omi-local',
        summary='Call Sam; confirm the venue, then email the team ' * 3,
        description='Line one\nLine two, with a comma',
        due=due,
        completed=True,
        completed_at=due,
        categories=['omi', 'work'],
        stamp=due,
    )
    assert all(len(line.encode()) <= 75 for line in text.split('\r\n'))
    assert 'SUMMARY:Call Sam\; confirm the venue\\, then email' in text.replace('\r\n ', '')
    (task,) = ical.parse_components(text)
    assert task.kind == 'VTODO' and task.uid == 'omi-task-abc@omi-local'
    assert task.summary == 'Call Sam; confirm the venue, then email the team ' * 3
    assert task.description == 'Line one\nLine two, with a comma'
    assert task.due == due and task.is_completed and task.completed_at == due
    assert task.categories == ['omi', 'work'] and task.last_modified == due


def test_event_all_day_and_timed_shapes():
    start = datetime(2026, 10, 27, 9, 30, tzinfo=timezone.utc)
    timed = ical.render_event(uid='e1', summary='Standup', start=start, duration_minutes=15, stamp=start)
    (event,) = ical.parse_components(timed)
    assert event.start == start and event.end == datetime(2026, 10, 27, 9, 45, tzinfo=timezone.utc)
    assert not event.all_day
    all_day = ical.render_event(uid='d1', summary='Omi: Your day', start=start, all_day=True, stamp=start)
    assert 'DTSTART;VALUE=DATE:20261027' in all_day and 'DTEND;VALUE=DATE:20261028' in all_day
    (day,) = ical.parse_components(all_day)
    assert day.all_day and day.start == datetime(2026, 10, 27, tzinfo=timezone.utc)


def test_parser_skips_alarms_and_reads_several_components():
    text = (
        'BEGIN:VCALENDAR\r\nVERSION:2.0\r\n'
        'BEGIN:VTODO\r\nUID:a\r\nSUMMARY:A\r\nSTATUS:NEEDS-ACTION\r\n'
        'BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:ring\r\nEND:VALARM\r\nEND:VTODO\r\n'
        'BEGIN:VEVENT\r\nUID:b\r\nSUMMARY:B\r\nDTSTART;TZID=Europe/Paris:20261027T100000\r\n'
        'PERCENT-COMPLETE:100\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'
    )
    task, event = ical.parse_components(text)
    assert task.uid == 'a' and task.description == '' and not task.is_completed
    # 10:00 Paris on 2026-10-27 is CET (+01:00): the TZID converts, it is not read as UTC.
    assert event.uid == 'b' and event.start == datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)
    assert event.is_completed  # PERCENT-COMPLETE counts


def test_tzid_times_convert_to_utc_and_unknown_zones_stay_floating():
    text = (
        'BEGIN:VCALENDAR\r\nVERSION:2.0\r\n'
        'BEGIN:VEVENT\r\nUID:summer\r\nSUMMARY:Lunch\r\n'
        'DTSTART;TZID=Europe/Berlin:20260715T140000\r\nDTEND;TZID="Europe/Berlin":20260715T150000\r\n'
        'END:VEVENT\r\n'
        'BEGIN:VTODO\r\nUID:due\r\nSUMMARY:Pay rent\r\nDUE;TZID=America/New_York:20260715T090000\r\nEND:VTODO\r\n'
        'BEGIN:VEVENT\r\nUID:odd\r\nSUMMARY:Odd zone\r\n'
        'DTSTART;TZID=Custom/Nowhere:20260715T140000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'
    )
    summer, due, odd = ical.parse_components(text)
    # 14:00 Berlin in July is CEST (+02:00); the quoted TZID on DTEND reads the same.
    assert summer.start == datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    assert summer.end == datetime(2026, 7, 15, 13, 0, tzinfo=timezone.utc) and not summer.all_day
    assert due.due == datetime(2026, 7, 15, 13, 0, tzinfo=timezone.utc)  # 09:00 New York is EDT (-04:00)
    assert odd.start == datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc)  # unknown zone: floating, as before


def test_set_todo_completion_patches_only_the_completion_properties():
    foreign = (
        'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//tasks.org//EN\r\n'
        'BEGIN:VTODO\r\nUID:phone-1\r\nSUMMARY:Buy milk\r\nSTATUS:NEEDS-ACTION\r\nPERCENT-COMPLETE:0\r\n'
        'LAST-MODIFIED:20261027T120000Z\r\nRRULE:FREQ=WEEKLY\r\nX-APPLE-SORT-ORDER:7\r\n'
        'BEGIN:VALARM\r\nACTION:DISPLAY\r\nSTATUS:KEEP-ME\r\nEND:VALARM\r\nEND:VTODO\r\nEND:VCALENDAR\r\n'
    )
    now = datetime(2026, 10, 28, 8, 0, tzinfo=timezone.utc)
    done = ical.set_todo_completion(foreign, completed=True, stamp=now)
    assert 'STATUS:COMPLETED' in done and 'PERCENT-COMPLETE:100' in done and 'COMPLETED:20261028T080000Z' in done
    # the foreign client's recurrence, X- props and alarm survive untouched
    assert 'RRULE:FREQ=WEEKLY' in done and 'X-APPLE-SORT-ORDER:7' in done and 'STATUS:KEEP-ME' in done
    assert done.count('STATUS:') == 2  # the VTODO's own and the alarm's, no stale NEEDS-ACTION left
    (task,) = ical.parse_components(done)
    assert task.is_completed and task.last_modified == now and task.summary == 'Buy milk'
    reopened = ical.set_todo_completion(done, completed=False, stamp=now)
    (task,) = ical.parse_components(reopened)
    assert not task.is_completed and 'COMPLETED:' not in reopened.replace('PERCENT-COMPLETE', '')
