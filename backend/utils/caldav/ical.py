"""The iCalendar text omi-local writes and reads. No library: the shapes are
few (one VTODO per task, one VEVENT per day summary or conversation event)
and every CalDAV server accepts this subset.

Dates are written in UTC (``Z``); all-day values use VALUE=DATE. Text is
escaped per RFC 5545 and lines are folded at 75 octets. Parsing unfolds lines,
unescapes text and reads the properties the sync needs; unknown properties
are kept verbatim in ``props`` so nothing is lost on inspection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

PRODID = '-//omi-local//CalDAV sync//EN'
TASK_UID_PREFIX = 'omi-task-'
DAY_UID_PREFIX = 'omi-day-'
CONVERSATION_EVENT_UID_PREFIX = 'omi-event-'
UID_DOMAIN = '@omi-local'


def escape_text(value: str) -> str:
    return (
        str(value or '')
        .replace('\\', '\\\\')
        .replace(';', '\\;')
        .replace(',', '\\,')
        .replace('\r\n', '\\n')
        .replace('\n', '\\n')
    )


def unescape_text(value: str) -> str:
    out: List[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == '\\' and i + 1 < len(value):
            nxt = value[i + 1]
            out.append('\n' if nxt in 'nN' else nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    return ''.join(out)


def fold(line: str) -> str:
    """Fold at 75 octets (continuation lines start with one space)."""
    encoded = line.encode('utf-8')
    if len(encoded) <= 75:
        return line
    parts: List[str] = []
    current = b''
    for ch in line:
        piece = ch.encode('utf-8')
        limit = 75 if not parts else 74
        if len(current) + len(piece) > limit:
            parts.append(current.decode('utf-8'))
            current = piece
        else:
            current += piece
    parts.append(current.decode('utf-8'))
    return '\r\n '.join(parts)


def unfold(text: str) -> List[str]:
    lines: List[str] = []
    for raw in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        if raw[:1] in (' ', '\t') and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def utc_stamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def date_stamp(value: date) -> str:
    return value.strftime('%Y%m%d')


def parse_stamp(value: str) -> Optional[datetime]:
    """DATE-TIME (UTC or floating, read as UTC) or DATE → aware UTC datetime."""
    value = (value or '').strip()
    for fmt in ('%Y%m%dT%H%M%SZ', '%Y%m%dT%H%M%S', '%Y%m%d'):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _component(name: str, lines: Sequence[str]) -> str:
    body = [f'BEGIN:{name}', *lines, f'END:{name}']
    return '\r\n'.join(
        ['BEGIN:VCALENDAR', 'VERSION:2.0', f'PRODID:{PRODID}', *(fold(l) for l in body), 'END:VCALENDAR', '']
    )


def render_todo(
    *,
    uid: str,
    summary: str,
    description: str = '',
    due: Optional[datetime] = None,
    completed: bool = False,
    completed_at: Optional[datetime] = None,
    created: Optional[datetime] = None,
    last_modified: Optional[datetime] = None,
    categories: Sequence[str] = (),
    priority: Optional[int] = None,
    stamp: Optional[datetime] = None,
) -> str:
    now = stamp or datetime.now(timezone.utc)
    lines = [f'UID:{uid}', f'DTSTAMP:{utc_stamp(now)}', f'SUMMARY:{escape_text(summary)}']
    if description:
        lines.append(f'DESCRIPTION:{escape_text(description)}')
    if created:
        lines.append(f'CREATED:{utc_stamp(created)}')
    lines.append(f'LAST-MODIFIED:{utc_stamp(last_modified or now)}')
    if due:
        lines.append(f'DUE:{utc_stamp(due)}')
    if categories:
        lines.append('CATEGORIES:' + ','.join(escape_text(c) for c in categories))
    if priority:
        lines.append(f'PRIORITY:{int(priority)}')
    if completed:
        lines += ['STATUS:COMPLETED', 'PERCENT-COMPLETE:100', f'COMPLETED:{utc_stamp(completed_at or now)}']
    else:
        lines += ['STATUS:NEEDS-ACTION', 'PERCENT-COMPLETE:0']
    return _component('VTODO', lines)


def render_event(
    *,
    uid: str,
    summary: str,
    start: datetime,
    end: Optional[datetime] = None,
    duration_minutes: Optional[int] = None,
    description: str = '',
    location: str = '',
    all_day: bool = False,
    categories: Sequence[str] = (),
    url: str = '',
    stamp: Optional[datetime] = None,
) -> str:
    now = stamp or datetime.now(timezone.utc)
    lines = [f'UID:{uid}', f'DTSTAMP:{utc_stamp(now)}', f'SUMMARY:{escape_text(summary)}']
    if all_day:
        day = start.date()
        lines += [f'DTSTART;VALUE=DATE:{date_stamp(day)}', f'DTEND;VALUE=DATE:{date_stamp(day + timedelta(days=1))}']
    else:
        finish = end or (start + timedelta(minutes=duration_minutes or 30))
        lines += [f'DTSTART:{utc_stamp(start)}', f'DTEND:{utc_stamp(finish)}']
    if description:
        lines.append(f'DESCRIPTION:{escape_text(description)}')
    if location:
        lines.append(f'LOCATION:{escape_text(location)}')
    if categories:
        lines.append('CATEGORIES:' + ','.join(escape_text(c) for c in categories))
    if url:
        lines.append(f'URL:{url}')
    lines.append(f'LAST-MODIFIED:{utc_stamp(now)}')
    return _component('VEVENT', lines)


@dataclass
class Component:
    kind: str  # VTODO | VEVENT | VJOURNAL
    uid: str = ''
    summary: str = ''
    description: str = ''
    location: str = ''
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    due: Optional[datetime] = None
    all_day: bool = False
    status: str = ''
    completed_at: Optional[datetime] = None
    last_modified: Optional[datetime] = None
    categories: List[str] = field(default_factory=list)
    props: Dict[str, str] = field(default_factory=dict)

    @property
    def is_completed(self) -> bool:
        return self.status.upper() == 'COMPLETED' or self.props.get('PERCENT-COMPLETE') == '100'


_PROP = re.compile(r'^([A-Za-z0-9-]+)((?:;[^:]*)?):(.*)$')


def parse_components(text: str) -> List[Component]:
    """Every VTODO/VEVENT/VJOURNAL in an iCalendar text (nested VALARMs skipped)."""
    out: List[Component] = []
    current: Optional[Component] = None
    depth = 0
    for line in unfold(text):
        match = _PROP.match(line)
        if not match:
            continue
        name, params, value = match.group(1).upper(), match.group(2), match.group(3)
        if name == 'BEGIN':
            kind = value.strip().upper()
            if kind in ('VTODO', 'VEVENT', 'VJOURNAL') and current is None:
                current = Component(kind=kind)
            elif current is not None:
                depth += 1
            continue
        if name == 'END':
            if current is not None and depth:
                depth -= 1
            elif current is not None and value.strip().upper() == current.kind:
                out.append(current)
                current = None
            continue
        if current is None or depth:
            continue
        current.props[name] = value
        if name == 'UID':
            current.uid = value.strip()
        elif name == 'SUMMARY':
            current.summary = unescape_text(value)
        elif name == 'DESCRIPTION':
            current.description = unescape_text(value)
        elif name == 'LOCATION':
            current.location = unescape_text(value)
        elif name == 'DTSTART':
            current.start = parse_stamp(value)
            current.all_day = 'VALUE=DATE' in params.upper() and 'DATE-TIME' not in params.upper()
        elif name == 'DTEND':
            current.end = parse_stamp(value)
        elif name == 'DUE':
            current.due = parse_stamp(value)
        elif name == 'STATUS':
            current.status = value.strip().upper()
        elif name == 'COMPLETED':
            current.completed_at = parse_stamp(value)
        elif name == 'LAST-MODIFIED':
            current.last_modified = parse_stamp(value)
        elif name == 'CATEGORIES':
            current.categories = [unescape_text(c).strip() for c in re.split(r'(?<!\\),', value) if c.strip()]
    return out
