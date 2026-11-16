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
    # 10:00 in Paris (CET after the October change) is 09:00 UTC
    assert event.uid == 'b' and event.start == datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)
    assert event.is_completed  # PERCENT-COMPLETE counts


def test_local_times_are_read_in_their_tzid():
    """What DAVx5 and Tasks.org write: local wall-clock times with an IANA TZID."""
    text = (
        'BEGIN:VCALENDAR\r\nVERSION:2.0\r\n'
        'BEGIN:VEVENT\r\nUID:e\r\nSUMMARY:School meeting\r\n'
        'DTSTART;TZID=Europe/Berlin:20260715T160000\r\nDTEND;TZID="Europe/Berlin":20260715T164500\r\n'
        'END:VEVENT\r\n'
        'BEGIN:VTODO\r\nUID:t\r\nSUMMARY:Passport\r\nDUE;TZID=America/New_York:20261201T090000\r\n'
        'COMPLETED;TZID=Europe/Berlin:20261130T120000\r\nEND:VTODO\r\n'
        'BEGIN:VEVENT\r\nUID:w\r\nSUMMARY:Outlook\r\n'
        'DTSTART;TZID=W. Europe Standard Time:20261027T100000\r\nEND:VEVENT\r\n'
        'BEGIN:VEVENT\r\nUID:z\r\nSUMMARY:UTC wins\r\nDTSTART;TZID=Europe/Berlin:20261027T100000Z\r\nEND:VEVENT\r\n'
        'END:VCALENDAR\r\n'
    )
    event, task, windows, utc = ical.parse_components(text)
    assert event.start == datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc)  # CEST, +2
    assert event.end == datetime(2026, 7, 15, 14, 45, tzinfo=timezone.utc)  # quoted TZID
    assert task.due == datetime(2026, 12, 1, 14, 0, tzinfo=timezone.utc)  # EST, -5
    assert task.completed_at == datetime(2026, 11, 30, 11, 0, tzinfo=timezone.utc)
    # a zone name we cannot resolve keeps the old reading (as UTC) rather than failing
    assert windows.start == datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc)
    # a UTC value ignores a stray TZID
    assert utc.start == datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc)
