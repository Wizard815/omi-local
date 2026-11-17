#!/usr/bin/env python3
"""CalDAV demo driver: Omi <-> CalDAV, both directions, checked from both ends.

Plays two parts against a running stack (omi-host/demo/caldav_demo.sh starts one;
README.md says how to point this at the Docker stack):

  * the Omi app, through the backend's HTTP API with a local-login session:
    action items, the day review, the family calendar's approve/edit/decline,
    and "Sync now" (POST /v1/caldav/sync);
  * a phone (DAVx5 + Tasks.org), through raw CalDAV against the server: PROPFIND,
    calendar-query REPORT, PUT and DELETE, with its own tiny iCalendar reader.
    Nothing here imports Omi's CalDAV client, so a bug there cannot hide itself.

Standard library only. Prints a table of checks and exits 1 when any FAILs. A GAP
is a direction the backend does not implement on purpose (see README.md); it is
reported but only fails the run with --strict.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))


def owner_tag(uid: str) -> str:
    """The owner part of the UIDs Omi writes for one Omi user (utils/caldav/sync.owner_tag)."""
    return 'u' + hashlib.sha256(uid.encode('utf-8')).hexdigest()[:8]
NS = {'d': 'DAV:', 'c': 'urn:ietf:params:xml:ns:caldav'}
PHONE_TZ = 'Europe/Berlin'  # what the "phone" writes its times in (TZID), as DAVx5 does


# ---- results ---------------------------------------------------------------------------------


@dataclass
class Check:
    area: str
    direction: str
    name: str
    status: str  # PASS | FAIL | GAP | INFO
    detail: str = ''


@dataclass
class Results:
    rows: List[Check] = field(default_factory=list)

    def add(self, area: str, direction: str, name: str, ok: bool, detail: str = '', *, gap: bool = False) -> bool:
        status = 'PASS' if ok else ('GAP' if gap else 'FAIL')
        self.rows.append(Check(area, direction, name, status, detail))
        mark = {'PASS': '  ok ', 'FAIL': ' FAIL', 'GAP': '  gap', 'INFO': ' info'}[status]
        print(f'[{mark}] {area:<9} {direction:<8} {name}' + (f'  -- {detail}' if detail and not ok else ''), flush=True)
        return ok

    def info(self, area: str, direction: str, name: str, detail: str) -> None:
        self.rows.append(Check(area, direction, name, 'INFO', detail))
        print(f'[ info] {area:<9} {direction:<8} {name}  -- {detail}', flush=True)

    def table(self) -> str:
        widths = [9, 8, max(len(r.name) for r in self.rows), 6]
        line = f"| {'Area':<{widths[0]}} | {'Dir':<{widths[1]}} | {'Check':<{widths[2]}} | {'Result':<{widths[3]}} | Detail"
        out = [line, '|' + '|'.join('-' * (w + 2) for w in widths) + '|--------']
        for r in self.rows:
            out.append(
                f'| {r.area:<{widths[0]}} | {r.direction:<{widths[1]}} | {r.name:<{widths[2]}} | {r.status:<{widths[3]}} | {r.detail}'
            )
        counts = {s: sum(1 for r in self.rows if r.status == s) for s in ('PASS', 'FAIL', 'GAP', 'INFO')}
        out.append(f"\n{counts['PASS']} passed, {counts['FAIL']} failed, {counts['GAP']} gaps, {counts['INFO']} info")
        return '\n'.join(out)


# ---- HTTP ------------------------------------------------------------------------------------


class HttpError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f'HTTP {status}: {body[:300]}')
        self.status = status
        self.body = body


def _request(method: str, url: str, *, body: Optional[bytes] = None, headers: Optional[Dict[str, str]] = None):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # LAN/loopback: never a proxy
    try:
        with opener.open(req, timeout=60) as response:
            return response.status, dict(response.headers), response.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers or {}), error.read().decode('utf-8', 'replace')


class Omi:
    """The app's side: the backend's HTTP API with a session token."""

    def __init__(self, base: str):
        self.base = base.rstrip('/')
        self.token = ''

    def call(self, method: str, path: str, payload: Any = None, *, ok=(200, 201, 204)) -> Any:
        headers = {'Content-Type': 'application/json'}
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'
        body = json.dumps(payload).encode() if payload is not None else None
        status, _, text = _request(method, self.base + path, body=body, headers=headers)
        if status not in ok:
            raise HttpError(status, text)
        return json.loads(text) if text.strip() else None

    def login(self, username: str, password: str) -> None:
        self.token = self.call('POST', '/v1/auth/local-login', {'username': username, 'password': password})[
            'session_token'
        ]

    def sync(self) -> Dict[str, Any]:
        return self.call('POST', '/v1/caldav/sync')

    def task(self, task_id: str) -> Optional[Dict[str, Any]]:
        try:
            return self.call('GET', f'/v1/action-items/{task_id}')
        except HttpError as error:
            if error.status == 404:
                return None
            raise

    def tasks(self) -> List[Dict[str, Any]]:
        return self.call('GET', '/v1/action-items?limit=200').get('action_items') or []


# ---- the phone: raw CalDAV + a tiny iCalendar reader ------------------------------------------


@dataclass
class DavObject:
    href: str
    etag: str
    text: str
    props: Dict[str, Tuple[str, str]]  # NAME -> (params, value), first VTODO/VEVENT only

    def value(self, name: str) -> str:
        return self.props.get(name, ('', ''))[1]

    def when(self, name: str) -> Optional[datetime]:
        params, value = self.props.get(name, ('', ''))
        return parse_ical_time(params, value)


def unfold(text: str) -> List[str]:
    lines: List[str] = []
    for raw in text.replace('\r\n', '\n').split('\n'):
        if raw[:1] in (' ', '\t') and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def unescape(value: str) -> str:
    return re.sub(r'\\(.)', lambda m: '\n' if m.group(1) in 'nN' else m.group(1), value)


def read_component(text: str) -> Dict[str, Tuple[str, str]]:
    props: Dict[str, Tuple[str, str]] = {}
    inside = False
    for line in unfold(text):
        if line in ('BEGIN:VTODO', 'BEGIN:VEVENT'):
            inside = True
            continue
        if line.startswith('BEGIN:') and inside:
            break  # a VALARM: the component's own properties are done
        if line in ('END:VTODO', 'END:VEVENT'):
            break
        if inside:
            m = re.match(r'^([A-Za-z0-9-]+)((?:;[^:]*)?):(.*)$', line)
            if m and m.group(1).upper() not in props:
                props[m.group(1).upper()] = (m.group(2), m.group(3))
    return props


def parse_ical_time(params: str, value: str) -> Optional[datetime]:
    value = value.strip()
    tzid = re.search(r'TZID=([^;:]+)', params or '')
    for fmt in ('%Y%m%dT%H%M%SZ', '%Y%m%dT%H%M%S', '%Y%m%d'):
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        if fmt.endswith('Z'):
            return parsed.replace(tzinfo=timezone.utc)
        zone = ZoneInfo(tzid.group(1).strip('"')) if tzid else timezone.utc
        return parsed.replace(tzinfo=zone).astimezone(timezone.utc)
    return None


def ical_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def ical_local(value: datetime, tz: str = PHONE_TZ) -> str:
    return value.astimezone(ZoneInfo(tz)).strftime('%Y%m%dT%H%M%S')


# The VTIMEZONE a phone sends with TZID times (DAVx5 always includes one).
BERLIN_VTIMEZONE = '\r\n'.join(
    [
        'BEGIN:VTIMEZONE',
        'TZID:Europe/Berlin',
        'BEGIN:STANDARD',
        'DTSTART:19701025T030000',
        'RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU',
        'TZOFFSETFROM:+0200',
        'TZOFFSETTO:+0100',
        'END:STANDARD',
        'BEGIN:DAYLIGHT',
        'DTSTART:19700329T020000',
        'RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU',
        'TZOFFSETFROM:+0100',
        'TZOFFSETTO:+0200',
        'END:DAYLIGHT',
        'END:VTIMEZONE',
    ]
)


class Phone:
    """DAVx5 + Tasks.org, reduced to the HTTP they send."""

    def __init__(self, base: str, user: str, password: str):
        self.base = base if base.endswith('/') else base + '/'
        self.auth = 'Basic ' + base64.b64encode(f'{user}:{password}'.encode()).decode()
        self.user = user

    def _url(self, href: str) -> str:
        return urllib.parse.urljoin(self.base, href)

    def dav(self, method: str, href: str, body: str = '', *, depth: Optional[str] = None, ctype='application/xml'):
        headers = {'Authorization': self.auth, 'Content-Type': f'{ctype}; charset=utf-8'}
        if depth is not None:
            headers['Depth'] = depth
        return _request(method, self._url(href), body=body.encode() if body else None, headers=headers)

    def collections(self) -> Dict[str, Dict[str, Any]]:
        """displayname -> {href, components} of the account's calendars (what DAVx5 discovers)."""
        body = (
            '<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop>'
            '<d:resourcetype/><d:displayname/><c:supported-calendar-component-set/></d:prop></d:propfind>'
        )
        status, _, text = self.dav('PROPFIND', f'{self.user}/', body, depth='1')
        if status != 207:
            raise HttpError(status, text)
        out: Dict[str, Dict[str, Any]] = {}
        for response in ElementTree.fromstring(text).findall('d:response', NS):
            if response.find('.//d:resourcetype/c:calendar', NS) is None:
                continue
            name = (response.findtext('.//d:displayname', default='', namespaces=NS) or '').strip()
            comps = [c.get('name') for c in response.findall('.//c:supported-calendar-component-set/c:comp', NS)]
            out[name] = {'href': response.findtext('d:href', namespaces=NS), 'components': comps}
        return out

    def make_calendar(self, href: str, name: str, component: str) -> None:
        body = (
            '<?xml version="1.0"?><c:mkcalendar xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:set><d:prop>'
            f'<d:displayname>{name}</d:displayname><c:supported-calendar-component-set><c:comp name="{component}"/>'
            '</c:supported-calendar-component-set></d:prop></d:set></c:mkcalendar>'
        )
        status, _, text = self.dav('MKCALENDAR', href, body)
        if status not in (200, 201):
            raise HttpError(status, text)

    def objects(self, collection_href: str, component: str) -> Dict[str, DavObject]:
        """UID -> object, via calendar-query REPORT (what DAVx5 runs on a sync)."""
        body = (
            '<?xml version="1.0"?><c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            '<d:prop><d:getetag/><c:calendar-data/></d:prop><c:filter><c:comp-filter name="VCALENDAR">'
            f'<c:comp-filter name="{component}"/></c:comp-filter></c:filter></c:calendar-query>'
        )
        status, _, text = self.dav('REPORT', collection_href, body, depth='1')
        if status != 207:
            raise HttpError(status, text)
        out: Dict[str, DavObject] = {}
        for response in ElementTree.fromstring(text).findall('d:response', NS):
            data = response.findtext('.//c:calendar-data', default='', namespaces=NS)
            if not data.strip():
                continue
            props = read_component(data)
            uid = props.get('UID', ('', ''))[1]
            out[uid] = DavObject(
                href=response.findtext('d:href', namespaces=NS) or '',
                etag=response.findtext('.//d:getetag', default='', namespaces=NS) or '',
                text=data,
                props=props,
            )
        return out

    def put(self, href: str, ics: str, *, etag: str = '') -> int:
        headers_etag = {'If-Match': etag} if etag else {}
        status, _, text = _request(
            'PUT',
            self._url(href),
            body=ics.encode(),
            headers={'Authorization': self.auth, 'Content-Type': 'text/calendar; charset=utf-8', **headers_etag},
        )
        if status not in (200, 201, 204):
            raise HttpError(status, text)
        return status

    def delete(self, href: str) -> int:
        status, _, text = _request('DELETE', self._url(href), headers={'Authorization': self.auth})
        if status not in (200, 204):
            raise HttpError(status, text)
        return status

    def edit(self, obj: DavObject, changes: Dict[str, Optional[str]]) -> str:
        """Rewrite properties of the object's component the way a phone app saves an edit:
        replaced/added/removed properties, a new LAST-MODIFIED and DTSTAMP, a bumped SEQUENCE.
        ``changes``: 'NAME' or 'NAME;PARAMS' -> value (None removes the property)."""
        now = ical_utc(datetime.now(timezone.utc))
        changes = {**changes, 'LAST-MODIFIED': now, 'DTSTAMP': now}
        names = {k.split(';', 1)[0].upper(): k for k in changes}
        lines = unfold(obj.text)
        out: List[str] = []
        inside = depth = 0
        seq_seen = False
        for line in lines:
            if line in ('BEGIN:VTODO', 'BEGIN:VEVENT'):
                inside = 1
                out.append(line)
                continue
            if inside and line.startswith('BEGIN:'):
                depth += 1
            if inside and depth == 0 and line in ('END:VTODO', 'END:VEVENT'):
                for name, key in names.items():
                    if changes[key] is not None:
                        out.append(f'{key}:{changes[key]}')
                if not seq_seen:
                    out.append('SEQUENCE:1')
                inside = 0
                out.append(line)
                continue
            if inside and line.startswith('END:') and depth:
                depth -= 1
            if inside and depth == 0:
                name = re.split(r'[;:]', line, 1)[0].upper()
                if name in names:
                    continue
                if name == 'SEQUENCE':
                    seq_seen = True
                    out.append(f'SEQUENCE:{int(line.split(":", 1)[1] or 0) + 1}')
                    continue
            out.append(line)
        text = '\r\n'.join(out) + '\r\n'
        if 'TZID=Europe/Berlin' in text and 'BEGIN:VTIMEZONE' not in text:
            text = text.replace('BEGIN:VTODO', BERLIN_VTIMEZONE + '\r\nBEGIN:VTODO', 1)
            text = text.replace('BEGIN:VEVENT', BERLIN_VTIMEZONE + '\r\nBEGIN:VEVENT', 1)
        self.put(obj.href, text, etag=obj.etag)
        return text


def new_todo(uid: str, summary: str, due: datetime) -> str:
    now = ical_utc(datetime.now(timezone.utc))
    return '\r\n'.join(
        [
            'BEGIN:VCALENDAR',
            'VERSION:2.0',
            'PRODID:+//IDN tasks.org//android-130804//EN',
            BERLIN_VTIMEZONE,
            'BEGIN:VTODO',
            f'UID:{uid}',
            f'DTSTAMP:{now}',
            f'CREATED:{now}',
            f'LAST-MODIFIED:{now}',
            f'SUMMARY:{summary}',
            f'DUE;TZID={PHONE_TZ}:{ical_local(due)}',
            'STATUS:NEEDS-ACTION',
            'END:VTODO',
            'END:VCALENDAR',
            '',
        ]
    )


def new_event(uid: str, summary: str, start: datetime, minutes: int, location: str = '') -> str:
    now = ical_utc(datetime.now(timezone.utc))
    return '\r\n'.join(
        [
            'BEGIN:VCALENDAR',
            'VERSION:2.0',
            'PRODID:-//bitfire web engineering//DAVx5 4.4//EN',
            BERLIN_VTIMEZONE,
            'BEGIN:VEVENT',
            f'UID:{uid}',
            f'DTSTAMP:{now}',
            f'LAST-MODIFIED:{now}',
            f'SUMMARY:{summary}',
            f'DTSTART;TZID={PHONE_TZ}:{ical_local(start)}',
            f'DTEND;TZID={PHONE_TZ}:{ical_local(start + timedelta(minutes=minutes))}',
            *( [f'LOCATION:{location}'] if location else [] ),
            'END:VEVENT',
            'END:VCALENDAR',
            '',
        ]
    )


# ---- seeding -----------------------------------------------------------------------------------


def seed(seed_cmd: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    with open(os.path.join(HERE, 'seed_day.py'), 'rb') as script:
        proc = subprocess.run(
            ['bash', '-c', seed_cmd],
            stdin=script,
            capture_output=True,
            env={**os.environ, 'OMI_DEMO_SEED': json.dumps(spec, default=str)},
            timeout=180,
        )
    lines = [l for l in proc.stdout.decode('utf-8', 'replace').splitlines() if l.strip()]
    if proc.returncode != 0 or not lines:
        raise RuntimeError(f'seed {spec["op"]} failed: {proc.stderr.decode("utf-8", "replace")[-2000:]}')
    return json.loads(lines[-1])


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)


def family_day(day: date, today: date) -> List[Dict[str, Any]]:
    """Three family conversations from ``day`` and what the extractor would have pulled from them.
    Starred, so the day's summary draft keeps them all (the offline stack has no model)."""
    d1, d2, d3, d5 = (today + timedelta(days=n) for n in (1, 2, 3, 5))
    return [
        {
            'title': "Mia's dentist appointment",
            'overview': "Talked about Mia's check-up at Dr. Weber's and calling to confirm it.",
            'started_at': at(day, 8, 10).isoformat(),
            'starred': True,
            'transcript': [
                "Mia's dentist check-up is on " + d2.strftime('%A') + ' at three.',
                "Can you call Dr. Weber's office tomorrow morning to confirm?",
            ],
            'events': [
                {
                    'title': 'Mia dentist check-up',
                    'description': "Dr. Weber's practice",
                    'start': at(d2, 15, 0).isoformat(),
                    'duration': 45,
                }
            ],
            'tasks': [("Call Dr. Weber's office to confirm Mia's appointment", at(d1, 10, 0))],
        },
        {
            'title': "Leo's soccer season",
            'overview': 'Practice moved to Thursday; Leo has outgrown his cleats.',
            'started_at': at(day, 12, 30).isoformat(),
            'starred': True,
            'transcript': ['Practice is ' + d3.strftime('%A') + ' at half five now.', 'And Leo needs new cleats.'],
            'events': [
                {'title': 'Leo soccer practice', 'description': 'Field 2', 'start': at(d3, 17, 30).isoformat(), 'duration': 90}
            ],
            'tasks': [('Buy new soccer cleats for Leo', at(d2, 18, 0))],
        },
        {
            'title': "Grandma's 80th birthday",
            'overview': "Planning grandma's birthday dinner at Luigi's and the cake.",
            'started_at': at(day, 18, 0).isoformat(),
            'starred': True,
            'transcript': ["Grandma's dinner is at seven, at Luigi's.", 'Someone has to pick up the cake at four.'],
            'events': [
                {'title': "Grandma's 80th birthday dinner", 'description': "Luigi's", 'start': at(d5, 19, 0).isoformat(), 'duration': 120},
                {'title': 'Pick up the birthday cake', 'description': 'Bakery Krause', 'start': at(d5, 16, 0).isoformat(), 'duration': 30},
            ],
            'tasks': [('Order the birthday cake', at(d3, 12, 0)), ("Book a table at Luigi's", at(d1, 18, 0))],
        },
    ]


# ---- the demo ----------------------------------------------------------------------------------


class Demo:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.omi = Omi(args.backend)
        self.phone = Phone(args.caldav, args.caldav_user, args.caldav_password)
        self.r = Results()
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.today = self.now.date()
        self.day = self.today - timedelta(days=1)  # yesterday: the day the family talked
        self.tasks: Dict[str, str] = {}  # description -> id
        self.conversations: List[Dict[str, Any]] = []
        self.tasks_href = ''
        self.events_href = ''
        # The calendars Omi syncs into: the bundled server's own ("Omi", "Omi Tasks"), or with
        # --isolate a pair made for this run (a real stack's family keeps its lists untouched).
        # Each Omi user gets their own pair on the shared account: "Omi – <name>", "Omi Tasks – <name>".
        self.events_name, self.tasks_name = 'Omi', 'Omi Tasks'
        self.isolated: List[str] = []
        self.tag = ''
        self.username = ''
        self.expected: Dict[str, str] = {}  # task id -> the SUMMARY the server should show

    # helpers
    def sync(self) -> Dict[str, Any]:
        report = self.omi.sync()
        if not report.get('ok'):
            print(f'       sync report: {json.dumps(report)}', flush=True)
        return report

    def todos(self) -> Dict[str, DavObject]:
        return self.phone.objects(self.tasks_href, 'VTODO')

    def events(self) -> Dict[str, DavObject]:
        return self.phone.objects(self.events_href, 'VEVENT')

    def todo_of(self, task_id: str) -> Optional[DavObject]:
        return self.todos().get(f'omi-task-{self.tag}-{task_id}@omi-local')

    def event_of(self, conversation_id: str, idx: int) -> Optional[DavObject]:
        return self.events().get(f'omi-event-{self.tag}-{conversation_id}-{idx}@omi-local')

    def day_of(self, date_str: str) -> Optional[DavObject]:
        return self.events().get(f'omi-day-{self.tag}-{date_str}@omi-local')

    def wait_for(self, predicate: Callable[[], Any], seconds: float, step: float = 2.0) -> Tuple[Any, float]:
        start = time.monotonic()
        while True:
            value = predicate()
            if value:
                return value, time.monotonic() - start
            if time.monotonic() - start > seconds:
                return value, time.monotonic() - start
            time.sleep(step)

    def guard(self, area: str, direction: str, name: str, fn: Callable[[], Tuple[bool, str]], *, gap: bool = False):
        try:
            ok, detail = fn()
        except Exception as error:  # noqa: BLE001 — one broken step is one FAIL row, the rest still run
            ok, detail = False, f'{type(error).__name__}: {error}'
        return self.r.add(area, direction, name, ok, detail, gap=gap)

    # ---- setup -------------------------------------------------------------------------------
    def setup(self) -> bool:
        username = self.args.omi_username or f'caldav-demo-{secrets.token_hex(3)}'
        password = self.args.omi_password or secrets.token_urlsafe(12)
        if self.args.seed_cmd:
            uid = seed(self.args.seed_cmd, {'op': 'account', 'username': username, 'password': password})['uid']
        else:
            uid = ''
        self.omi.login(username, password)
        if not uid:
            uid = 'unknown'
        self.uid = uid
        self.tag = owner_tag(uid)
        self.username = username
        self.events_name, self.tasks_name = f'Omi – {username}', f'Omi Tasks – {username}'
        self.r.add('setup', '-', f'local login as {username}', True)

        settings = self.omi.call('GET', '/v1/caldav/settings')
        self.r.add(
            'setup',
            '-',
            'CalDAV account is the bundled default',
            settings.get('configured') and settings.get('bundled'),
            json.dumps({k: settings.get(k) for k in ('configured', 'bundled', 'url', 'username')}),
        )
        if self.args.isolate:
            self.isolate(settings)
        test = self.omi.call('POST', '/v1/caldav/test')
        self.r.add('setup', '-', 'Test connection reaches the server', bool(test.get('ok')), test.get('error') or '')
        return bool(settings.get('configured') and test.get('ok'))

    def isolate(self, settings: Dict[str, Any]) -> None:
        """A calendar and a task list for this run only, set through the app's settings API
        (Settings -> Calendar & tasks), on the same server and account the stack uses."""
        tag = secrets.token_hex(3)
        self.events_name, self.tasks_name = f'Omi demo {tag}', f'Omi demo tasks {tag}'
        events_path = f'{self.args.caldav_user}/omi-demo-{tag}/'
        tasks_path = f'{self.args.caldav_user}/omi-demo-{tag}-tasks/'
        self.phone.make_calendar(events_path, self.events_name, 'VEVENT')
        self.phone.make_calendar(tasks_path, self.tasks_name, 'VTODO')
        self.isolated = [events_path, tasks_path]
        # The backend reaches the server at its own URL (inside the container: 127.0.0.1).
        server = settings.get('url') or self.args.caldav
        stored = self.omi.call(
            'PUT',
            '/v1/caldav/settings',
            {
                'url': server,
                'username': self.args.caldav_user,
                'password': self.args.caldav_password,
                'calendar_href': urllib.parse.urljoin(server, '/' + events_path),
                'tasks_href': urllib.parse.urljoin(server, '/' + tasks_path),
            },
        )
        self.r.add(
            'setup',
            '-',
            'run-only calendars set through PUT /v1/caldav/settings (--isolate)',
            stored.get('configured') and not stored.get('bundled'),
            f"{stored.get('calendar_href')} {stored.get('tasks_href')}",
        )

    def cleanup(self) -> None:
        if not self.isolated or self.args.keep_data:
            return
        for path in self.isolated:
            try:
                self.phone.delete(path)
            except Exception as error:  # noqa: BLE001
                print(f'cleanup: could not delete {path}: {error}', flush=True)
        try:
            self.omi.call('DELETE', '/v1/caldav/settings')
        except Exception as error:  # noqa: BLE001
            print(f'cleanup: could not reset the CalDAV settings: {error}', flush=True)
        print(f'cleanup: deleted {", ".join(self.isolated)} and the demo user\'s CalDAV settings', flush=True)

    def seed_day(self) -> None:
        if not self.args.seed_cmd:
            raise SystemExit('--seed-cmd is required: conversations are seeded with their extraction results')
        plan = family_day(self.day, self.today)
        ids = seed(self.args.seed_cmd, {'op': 'conversations', 'uid': self.uid, 'conversations': plan})['ids']
        specs = []
        for cid, item in zip(ids, plan):
            item['id'] = cid
            self.conversations.append(item)
            for description, due in item['tasks']:
                specs.append({'description': description, 'due_at': due.isoformat(), 'conversation_id': cid,
                              'created_at': item['started_at']})
        # One more heard task, which the user will decline.
        specs.append({'description': 'Ask about the school trip', 'conversation_id': ids[0],
                      'created_at': plan[0]['started_at']})
        # What conversation processing stores for extracted action items: waiting for the user's OK.
        task_ids = seed(self.args.seed_cmd, {'op': 'tasks', 'uid': self.uid, 'tasks': specs})['ids']
        for spec, task_id in zip(specs, task_ids):
            self.tasks[spec['description']] = task_id
            self.expected[task_id] = spec['description']
        if self.args.allglance_url:
            seed(
                self.args.seed_cmd,
                {'op': 'allglance', 'uid': self.uid, 'url': self.args.allglance_url, 'token': 'omi-caldav-demo-stub'},
            )
        self.r.add(
            'setup',
            '-',
            f'seeded {len(ids)} family conversations ({self.day}) and {len(self.tasks)} action items',
            True,
        )

    # ---- send: Omi -> CalDAV ----------------------------------------------------------------
    def send(self) -> None:
        date_str = self.day.isoformat()
        # The night's job writes a summary DRAFT (here: the app's "Summarize for me", the same call);
        # the user adds a line to it. Nothing has been approved yet.
        self.omi.call('GET', f'/v1/users/day-reviews/{date_str}')
        draft = self.omi.call('POST', f'/v1/users/day-reviews/{date_str}/bundle')
        self.r.add('day', '-', 'the day summary is prepared as a draft', draft.get('status') == 'draft', draft.get('status'))
        self.omi.call(
            'POST',
            f'/v1/users/day-reviews/{date_str}/corrections',
            {'type': 'paragraph', 'text': "Family day: Mia's dentist, Leo's soccer, grandma's 80th."},
        )
        report = self.sync()
        self.r.add('sync', '-', 'Sync now (POST /v1/caldav/sync) reports no errors', bool(report.get('ok')), json.dumps(report))

        def collections():
            found = self.phone.collections()
            tasks, events = found.get(self.tasks_name) or {}, found.get(self.events_name) or {}
            self.tasks_href, self.events_href = tasks.get('href') or '', events.get('href') or ''
            ok = 'VTODO' in tasks.get('components', []) and 'VEVENT' in events.get('components', [])
            return ok, json.dumps({self.events_name: events, self.tasks_name: tasks})

        what = f'the phone sees this person\'s own "{self.events_name}" (VEVENT) and "{self.tasks_name}" (VTODO)'
        if not self.guard('setup', 'send', what, collections):
            raise SystemExit('no calendars on the server; nothing else can be checked')

        # ---- nothing Omi heard is on the server before the user's OK ----
        waiting = self.omi.call('GET', '/v1/approvals')
        self.r.add(
            'approve',
            '-',
            'GET /v1/approvals lists the heard tasks, events and the draft day',
            waiting.get('task_count') == len(self.tasks) and waiting.get('event_count', 0) >= 4
            and date_str in (waiting.get('draft_dates') or []),
            f"tasks={waiting.get('task_count')} events={waiting.get('event_count')} drafts={waiting.get('draft_dates')}",
        )
        self.guard(
            'approve',
            'send',
            'unapproved tasks are NOT on the server',
            lambda: ((n := sum(1 for t in self.tasks.values() if self.todo_of(t))) == 0, f'{n} of {len(self.tasks)} pushed'),
        )
        self.guard(
            'approve',
            'send',
            'unapproved events are NOT on the server',
            lambda: (
                (n := sum(1 for c in self.conversations for i, _ in enumerate(c['events']) if self.event_of(c['id'], i))) == 0,
                f'{n} pushed',
            ),
        )
        self.guard(
            'approve',
            'send',
            'the draft day summary is NOT on the server',
            lambda: ((obj := self.day_of(date_str)) is None, 'absent' if obj is None else 'a draft was pushed'),
        )
        marks = {t['id']: t.get('approval') for t in self.omi.tasks()}
        self.r.add(
            'approve',
            '-',
            'the task list marks them approval=pending (the app shows them only under "Waiting for your OK")',
            all(marks.get(t) == 'pending' for t in self.tasks.values()),
            json.dumps({k: marks.get(v) for k, v in list(self.tasks.items())[:2]}),
        )

        # ---- the user approves: tasks (one edited), declines one (and undoes it once), the day ----
        cake_id = self.tasks['Order the birthday cake']
        for description, task_id in self.tasks.items():
            if description == 'Ask about the school trip':
                continue
            body = {'description': 'Order the birthday cake (chocolate)'} if task_id == cake_id else {}
            self.omi.call('POST', f'/v1/approvals/tasks/{task_id}/approve', body)
        self.expected[cake_id] = 'Order the birthday cake (chocolate)'
        trip = self.tasks.pop('Ask about the school trip')
        self.omi.call('POST', f'/v1/approvals/tasks/{trip}/decline', {})
        undone = self.omi.call('POST', f'/v1/approvals/tasks/{trip}/decline', {'declined': False})
        self.r.add('approve', '-', 'a decline can be undone (the task waits again)', (undone.get('task') or {}).get('approval') == 'pending')
        self.omi.call('POST', f'/v1/approvals/tasks/{trip}/decline', {})
        approved_day = self.omi.call('POST', f'/v1/users/day-reviews/{date_str}/approve')
        self.r.add('approve', '-', 'Approve finalizes the draft day', approved_day.get('status') == 'finalized', approved_day.get('status'))
        # Calendar check: every heard event but Mia's (approved with edits further down).
        mia = self.conversations[0]
        for conversation in self.conversations:
            for idx, _ in enumerate(conversation['events']):
                if conversation is mia:
                    continue
                self.omi.call('POST', '/v1/family-calendar/events', {'conversation_id': conversation['id'], 'event_idx': idx})
        self.sync()

        # Tasks
        def task_pushed(description: str):
            def check():
                task_id = self.tasks[description]
                due = next(d for c in self.conversations for t, d in c['tasks'] if t == description)
                obj = self.todo_of(task_id)
                if obj is None:
                    return False, 'no VTODO'
                ok = (
                    unescape(obj.value('SUMMARY')) == self.expected[task_id]
                    and obj.when('DUE') == due
                    and obj.value('STATUS') == 'NEEDS-ACTION'
                )
                return ok, f"SUMMARY={unescape(obj.value('SUMMARY'))!r} DUE={obj.value('DUE')} STATUS={obj.value('STATUS')}"

            return check

        for description in self.tasks:
            self.guard('task', 'send', f'approved -> VTODO for "{self.expected[self.tasks[description]]}"', task_pushed(description))
        self.guard(
            'approve',
            'send',
            'a declined task stays off the server',
            lambda: ((obj := self.todo_of(trip)) is None, 'absent' if obj is None else 'declined task pushed'),
        )

        # Events and the day summary
        for conversation in self.conversations:
            for idx, event in enumerate(conversation['events']):

                def check(conversation=conversation, idx=idx, event=event):
                    obj = self.event_of(conversation['id'], idx)
                    if conversation is mia:
                        return obj is None, 'not approved yet: absent' if obj is None else 'pushed without approval'
                    if obj is None:
                        return False, 'no VEVENT'
                    start = datetime.fromisoformat(event['start'])
                    end = start + timedelta(minutes=event['duration'])
                    ok = unescape(obj.value('SUMMARY')) == event['title'] and obj.when('DTSTART') == start and obj.when('DTEND') == end
                    return ok, f"SUMMARY={unescape(obj.value('SUMMARY'))!r} DTSTART={obj.value('DTSTART')} DTEND={obj.value('DTEND')}"

                label = 'still waiting: no VEVENT' if conversation is mia else 'approved -> VEVENT'
                self.guard('event', 'send', f'{label} for "{event["title"]}"', check)

        def day_summary():
            obj = self.day_of(date_str)
            if obj is None:
                return False, 'no all-day VEVENT'
            params, value = obj.props['DTSTART']
            ok = 'VALUE=DATE' in params and value == self.day.strftime('%Y%m%d') and 'grandma' in unescape(obj.value('DESCRIPTION'))
            return ok, f"UID=omi-day-{self.tag}-{date_str} SUMMARY={unescape(obj.value('SUMMARY'))!r}"

        self.guard('day', 'send', 'approved day summary as an all-day VEVENT (owner-scoped UID)', day_summary)

        # Reopening the approved day takes its summary off until it is approved again.
        self.omi.call('POST', f'/v1/users/day-reviews/{date_str}/reopen')
        self.sync()
        self.guard(
            'day',
            'send',
            'reopen takes the summary off the calendar',
            lambda: ((obj := self.day_of(date_str)) is None, 'gone' if obj is None else 'still on the server'),
        )
        again = self.omi.call('POST', f'/v1/users/day-reviews/{date_str}/approve')
        self.sync()
        self.guard(
            'day',
            'send',
            '... and approving the draft again puts it back',
            lambda: (again.get('status') == 'finalized' and self.day_of(date_str) is not None, again.get('status')),
        )

        # Edits in Omi reach the server.
        cleats = self.tasks['Buy new soccer cleats for Leo']
        new_due = at(self.today + timedelta(days=4), 9, 0)
        self.omi.call(
            'PATCH', f'/v1/action-items/{cleats}', {'description': 'Buy soccer cleats (size 34) for Leo', 'due_at': new_due.isoformat()}
        )
        self.sync()

        def edited():
            obj = self.todo_of(cleats)
            ok = obj is not None and unescape(obj.value('SUMMARY')) == 'Buy soccer cleats (size 34) for Leo' and obj.when('DUE') == new_due
            return ok, f"SUMMARY={obj and unescape(obj.value('SUMMARY'))!r} DUE={obj and obj.value('DUE')}"

        self.guard('task', 'send', 'edit in Omi (title + due) updates the VTODO', edited)

        call = self.tasks["Call Dr. Weber's office to confirm Mia's appointment"]
        self.omi.call('PATCH', f'/v1/action-items/{call}/completed?completed=true')
        self.sync()

        def completed():
            obj = self.todo_of(call)
            ok = obj is not None and obj.value('STATUS') == 'COMPLETED' and bool(obj.value('COMPLETED'))
            return ok, f"STATUS={obj and obj.value('STATUS')} COMPLETED={obj and obj.value('COMPLETED')}"

        self.guard('task', 'send', 'completing in Omi marks the VTODO COMPLETED', completed)

        luigi = self.tasks["Book a table at Luigi's"]
        self.omi.call('DELETE', f'/v1/action-items/{luigi}')
        self.sync()
        self.guard(
            'task', 'send', 'deleting in Omi removes the VTODO', lambda: ((gone := self.todo_of(luigi) is None), 'VTODO gone' if gone else 'VTODO still on the server')
        )

        # Family calendar: approve with the user's corrections, and decline.
        mia = self.conversations[0]
        cake = self.conversations[2]
        fc = self.omi.call('GET', '/v1/family-calendar')
        pending = {(e['conversation_id'], e['event_idx']) for e in fc.get('pending') or []}
        approved_ones = {(e['conversation_id'], e['event_idx']) for e in fc.get('recent') or [] if e.get('approved')}
        self.r.add(
            'family',
            '-',
            'the Calendar check lists Mia\'s event as waiting and the approved ones as reviewed',
            (fc.get('configured') or not self.args.allglance_url)
            and (mia['id'], 0) in pending
            and (cake['id'], 1) in approved_ones,
            f"configured={fc.get('configured')} pending={len(pending)} approved={len(approved_ones)}",
        )
        moved = at(self.today + timedelta(days=2), 16, 15)
        if not self.args.allglance_url:
            self.r.info(
                'family', 'send', 'approve with edits: skipped', 'no --allglance-url (approve writes to the family calendar)'
            )
            approved = {'ok': False, 'skipped': True}
        else:
            approved = self.omi.call(
                'POST',
                '/v1/family-calendar/events',
                {
                    'conversation_id': mia['id'],
                    'event_idx': 0,
                    'title': 'Mia dentist check-up (Dr. Weber)',
                    'start': moved.isoformat(),
                    'duration': 30,
                },
            )
            sent = ''
            if self.args.allglance_log and os.path.exists(self.args.allglance_log):
                with open(self.args.allglance_log, encoding='utf-8') as log:
                    sent = log.read().strip().splitlines()[-1]
            self.r.add(
                'family',
                'send',
                'approve with edits (title, time, length) is accepted',
                bool(approved.get('ok')),
                approved.get('error') or f'gateway got {sent}',
            )
        self.sync()

        def approved_on_server():
            obj = self.event_of(mia['id'], 0)
            ok = (
                obj is not None
                and unescape(obj.value('SUMMARY')) == 'Mia dentist check-up (Dr. Weber)'
                and obj.when('DTSTART') == moved
                and obj.when('DTEND') == moved + timedelta(minutes=30)
            )
            return ok, f"SUMMARY={obj and unescape(obj.value('SUMMARY'))!r} DTSTART={obj and obj.value('DTSTART')}"

        if not approved.get('skipped'):
            self.guard('family', 'send', 'the approved edit updates the VEVENT', approved_on_server)

        declined = self.omi.call('POST', '/v1/family-calendar/events/decline', {'conversation_id': cake['id'], 'event_idx': 1})
        self.r.add('family', 'send', 'decline is accepted', bool(declined.get('ok')), declined.get('error') or '')
        self.sync()
        self.guard(
            'family',
            'send',
            'a declined event is removed from the calendar',
            lambda: ((gone := self.event_of(cake['id'], 1) is None), 'VEVENT gone' if gone else 'declined VEVENT still on the server'),
        )

        # Without "Sync now": the change-triggered pass (~20 s) carries a new task.
        wrap = self.omi.call(
            'POST', '/v1/action-items', {'description': "Wrap grandma's present", 'due_at': at(self.today + timedelta(days=4), 20, 0).isoformat()}
        )['id']
        self.tasks["Wrap grandma's present"] = wrap
        obj, waited = self.wait_for(lambda: self.todo_of(wrap), self.args.scheduler_wait)
        self.r.add(
            'task',
            'send',
            'a task made in the app counts as approved and reaches the server by itself (no Sync now)',
            obj is not None,
            f'after {waited:.0f}s' if obj else f'not there after {waited:.0f}s',
        )
        if obj is not None:
            self.r.info('task', 'send', 'scheduler latency for a new task', f'{waited:.0f}s')

    # ---- receive: phone -> CalDAV -> Omi ----------------------------------------------------
    def receive(self) -> None:
        todos = self.todos()
        cake_id = self.tasks['Order the birthday cake']
        cleats_id = self.tasks['Buy new soccer cleats for Leo']
        wrap_id = self.tasks["Wrap grandma's present"]

        # 1. Tick a task off in Tasks.org.
        cake = todos[f'omi-task-{self.tag}-{cake_id}@omi-local']
        self.phone.edit(cake, {'STATUS': 'COMPLETED', 'PERCENT-COMPLETE': '100', 'COMPLETED': ical_utc(datetime.now(timezone.utc))})
        self.sync()
        self.guard(
            'task',
            'receive',
            'completed on the phone -> completed in Omi',
            lambda: ((done := bool((self.omi.task(cake_id) or {}).get('completed'))), f'Omi completed={done}'),
        )

        # 2. Rename and re-date a task (Tasks.org writes DUE with the phone's TZID).
        wrap = self.todos()[f'omi-task-{self.tag}-{wrap_id}@omi-local']
        phone_due = at(self.today + timedelta(days=5), 7, 30)
        self.phone.edit(
            wrap,
            {'SUMMARY': "Wrap grandma's present and write the card", f'DUE;TZID={PHONE_TZ}': ical_local(phone_due)},
        )
        self.sync()

        def renamed():
            item = self.omi.task(wrap_id) or {}
            return item.get('description') == "Wrap grandma's present and write the card", f"description={item.get('description')!r}"

        self.guard('task', 'receive', 'renamed on the phone -> renamed in Omi', renamed)

        def redated():
            item = self.omi.task(wrap_id) or {}
            due = item.get('due_at')
            got = datetime.fromisoformat(due.replace('Z', '+00:00')) if due else None
            return got == phone_due, f'due_at={due} want {phone_due.isoformat()} ({ical_local(phone_due)} {PHONE_TZ})'

        self.guard('task', 'receive', 'due date changed on the phone (TZID) -> due_at in Omi', redated)

        # 3. Delete a task on the phone.
        cleats = self.todos()[f'omi-task-{self.tag}-{cleats_id}@omi-local']
        self.phone.delete(cleats.href)
        self.sync()
        self.guard(
            'task',
            'receive',
            'deleted on the phone -> deleted in Omi',
            lambda: ((gone := self.omi.task(cleats_id) is None), 'GET /v1/action-items/{id} -> 404' if gone else 'the Omi task is still there'),
        )
        self.sync()
        self.guard(
            'task',
            'receive',
            '... and Omi does not push it back',
            lambda: ((gone := self.todo_of(cleats_id) is None), 'still gone after another sync' if gone else 'the VTODO came back'),
        )

        # 4. A brand-new task made on the phone.
        phone_uid = f'{uuid.uuid4()}'
        passport_due = at(self.today + timedelta(days=10), 9, 0)
        self.phone.put(
            urllib.parse.urljoin(self.tasks_href, f'{phone_uid}.ics'), new_todo(phone_uid, "Renew Leo's passport", passport_due)
        )
        self.sync()
        imported = [t for t in self.omi.tasks() if t.get('description') == "Renew Leo's passport"]

        def imported_ok():
            if len(imported) != 1:
                return False, f'{len(imported)} matching Omi tasks'
            due = imported[0].get('due_at')
            got = datetime.fromisoformat(due.replace('Z', '+00:00')) if due else None
            return got == passport_due, f"source={imported[0].get('source')} due_at={due}"

        self.guard('task', 'receive', 'new task created on the phone -> new Omi task (due from TZID)', imported_ok)
        self.sync()  # the next pass pushes Omi's tasks: the imported one must not come back as a copy
        self.guard(
            'task',
            'receive',
            '... and the phone still has it once (no omi-task copy)',
            lambda: (
                (n := sum(1 for o in self.todos().values() if unescape(o.value('SUMMARY')) == "Renew Leo's passport")) == 1,
                f'{n} VTODOs with that title on the server',
            ),
        )
        if imported:
            obj = self.todos()[phone_uid]
            self.phone.edit(obj, {'STATUS': 'COMPLETED', 'COMPLETED': ical_utc(datetime.now(timezone.utc))})
            self.sync()
            self.guard(
                'task',
                'receive',
                '... then completed on the phone -> completed in Omi',
                lambda: ((done := bool((self.omi.task(imported[0]['id']) or {}).get('completed'))), f'Omi completed={done}'),
            )

        # 5. Conflict: both sides edit the same task before a sync.
        conflict = self.todos()[f'omi-task-{self.tag}-{wrap_id}@omi-local']
        self.phone.edit(conflict, {'SUMMARY': 'Phone title'})
        time.sleep(1.1)
        self.omi.call('PATCH', f'/v1/action-items/{wrap_id}', {'description': 'Omi title'})
        self.sync()
        on_phone = unescape((self.todo_of(wrap_id) or DavObject('', '', '', {})).value('SUMMARY'))
        in_omi = (self.omi.task(wrap_id) or {}).get('description')
        self.r.info('task', 'both', 'conflict: both edited before one sync', f'server={on_phone!r} omi={in_omi!r}')
        self.r.add(
            'task', 'both', 'conflict ends converged (both sides show the same title)', on_phone == in_omi, f'{on_phone!r} vs {in_omi!r}'
        )

        # 6. Events made on the phone land in Omi's calendar view (GET /v1/device-calendar/events).
        ev_uid = f'{uuid.uuid4()}'
        ev_start = at(self.today + timedelta(days=3), 16, 0)
        ev_href = urllib.parse.urljoin(self.events_href, f'{ev_uid}.ics')
        self.phone.put(ev_href, new_event(ev_uid, 'Parent-teacher meeting', ev_start, 45, 'Room 12'))
        self.sync()

        def omi_event():
            return next((e for e in self.omi.call('GET', '/v1/device-calendar/events')['events'] if e.get('event_id') == ev_uid), None)

        def created_ok():
            e = omi_event()
            if not e:
                return False, 'not in /v1/device-calendar/events'
            return (
                e['title'] == 'Parent-teacher meeting' and e['start_ms'] == int(ev_start.timestamp() * 1000) and e['location'] == 'Room 12',
                f"title={e['title']!r} start={e['start']} (want {ev_start.isoformat()})",
            )

        self.guard('event', 'receive', 'event created on the phone (TZID) -> Omi calendar, right time', created_ok)
        obj = self.events()[ev_uid]
        ev_moved = ev_start + timedelta(hours=1, minutes=30)
        self.phone.edit(
            obj,
            {
                'SUMMARY': 'Parent-teacher meeting (moved)',
                f'DTSTART;TZID={PHONE_TZ}': ical_local(ev_moved),
                f'DTEND;TZID={PHONE_TZ}': ical_local(ev_moved + timedelta(minutes=45)),
            },
        )
        self.sync()

        def moved_ok():
            e = omi_event()
            ok = bool(e) and e['title'] == 'Parent-teacher meeting (moved)' and e['start_ms'] == int(ev_moved.timestamp() * 1000)
            return ok, f"{e and e['title']!r} {e and e['start']}"

        self.guard('event', 'receive', 'event moved + renamed on the phone -> Omi calendar', moved_ok)
        self.phone.delete(self.events()[ev_uid].href)
        self.sync()
        self.guard('event', 'receive', 'event deleted on the phone -> gone from Omi calendar', lambda: ((gone := omi_event() is None), 'not listed any more' if gone else 'still listed'))

        # 7. The phone edits an event Omi pushed (the extracted soccer practice).
        leo = self.conversations[1]
        soccer = self.event_of(leo['id'], 0)
        soccer_moved = at(self.today + timedelta(days=3), 18, 0)
        self.phone.edit(
            soccer,
            {
                'SUMMARY': 'Leo soccer practice (new time)',
                f'DTSTART;TZID={PHONE_TZ}': ical_local(soccer_moved),
                f'DTEND;TZID={PHONE_TZ}': ical_local(soccer_moved + timedelta(minutes=90)),
            },
        )
        self.sync()

        def omi_soccer():
            fc = self.omi.call('GET', f"/v1/family-calendar?conversation_id={leo['id']}")
            return next(e for e in (fc.get('pending') or []) + (fc.get('recent') or []) if e['event_idx'] == 0)

        def soccer_ok():
            e = omi_soccer()
            got = datetime.fromisoformat(e['start'].replace('Z', '+00:00'))
            return e['title'] == 'Leo soccer practice (new time)' and got == soccer_moved, f"Omi has {e['title']!r} at {e['start']}"

        self.guard('event', 'receive', 'Omi-made event moved + renamed on the phone -> Omi event', soccer_ok)

        def soccer_kept():
            obj = self.event_of(leo['id'], 0)
            ok = obj is not None and obj.when('DTSTART') == soccer_moved
            return ok, f"server DTSTART={obj and obj.value('DTSTART')}"

        self.guard('event', 'receive', "... and the next sync keeps the phone's version on the server", soccer_kept)

        # 8. The phone deletes an event Omi pushed (grandma's dinner).
        grandma = self.conversations[2]
        self.phone.delete(self.event_of(grandma['id'], 0).href)
        self.sync()

        def dinner_gone():
            fc = self.omi.call('GET', f"/v1/family-calendar?conversation_id={grandma['id']}")
            e = next(e for e in (fc.get('pending') or []) + (fc.get('recent') or []) if e['event_idx'] == 0)
            return bool(e.get('declined')), f"Omi event declined={e.get('declined')}; server has it: {self.event_of(grandma['id'], 0) is not None}"

        self.guard('event', 'receive', 'Omi-made event deleted on the phone -> declined in Omi', dinner_gone)

        # 9. Without "Sync now": the periodic full pass picks up a phone change. First let the
        # change-triggered pass drain (it runs every 20 s for users Omi itself marked), so
        # what picks the phone's edit up can only be the full pass.
        time.sleep(self.args.dirty_settle)
        call_id = self.tasks["Call Dr. Weber's office to confirm Mia's appointment"]
        obj = self.todo_of(call_id)
        self.phone.edit(obj, {'STATUS': 'NEEDS-ACTION', 'PERCENT-COMPLETE': '0', 'COMPLETED': None})
        _, waited = self.wait_for(
            lambda: not (self.omi.task(call_id) or {}).get('completed'), self.args.full_pass_wait, step=3.0
        )
        reopened = not (self.omi.task(call_id) or {}).get('completed')
        self.r.add(
            'task',
            'receive',
            're-opened on the phone -> re-opened in Omi by itself (periodic full pass)',
            reopened,
            f'after {waited:.0f}s' if reopened else f'still completed after {waited:.0f}s',
        )

    # ---- a second person on the same CalDAV account, and auto-approve ------------------------
    def second_person(self) -> None:
        """Bob uses the same Omi server, so the same CalDAV login. He gets his own calendar and
        task list, his summary for the same date does not touch Alice's, approve works without a
        family-calendar gateway, and auto-approve is off until he turns it on."""
        if not self.args.seed_cmd:
            return
        date_str = self.day.isoformat()
        name = f'caldav-demo-bob-{secrets.token_hex(3)}'
        password = secrets.token_urlsafe(12)
        bob_uid = seed(self.args.seed_cmd, {'op': 'account', 'username': name, 'password': password})['uid']
        bob = Omi(self.args.backend)
        bob.login(name, password)
        bob_tag = owner_tag(bob_uid)
        self.r.add('people', '-', f'second Omi user {name} on the same server', True)
        start = at(self.today + timedelta(days=6), 10, 0)
        (cid,) = seed(
            self.args.seed_cmd,
            {
                'op': 'conversations',
                'uid': bob_uid,
                'conversations': [
                    {
                        'title': 'Bike repair',
                        'overview': 'Bob talked about getting the bike fixed.',
                        'started_at': at(self.day, 9, 0).isoformat(),
                        'starred': True,
                        'transcript': ['The bike shop can take it next week.'],
                        'events': [{'title': 'Bike shop appointment', 'start': start.isoformat(), 'duration': 30}],
                    }
                ],
            },
        )['ids']
        (bob_task,) = seed(
            self.args.seed_cmd,
            {
                'op': 'tasks',
                'uid': bob_uid,
                'tasks': [{'description': 'Pump up the bike tyres', 'conversation_id': cid,
                           'created_at': at(self.day, 9, 5).isoformat()}],
            },
        )['ids']
        bob.call('POST', f'/v1/users/day-reviews/{date_str}/bundle')
        bob.call('POST', f'/v1/users/day-reviews/{date_str}/corrections', {'type': 'paragraph', 'text': "Bob's day: the bike."})
        bob.call('POST', f'/v1/users/day-reviews/{date_str}/approve')
        no_gateway = bob.call('POST', '/v1/family-calendar/events', {'conversation_id': cid, 'event_idx': 0})
        self.r.add(
            'people',
            '-',
            'approve works without a family-calendar gateway (means: add to my CalDAV calendar)',
            bool(no_gateway.get('ok')) and (no_gateway.get('event') or {}).get('approved') is True,
            no_gateway.get('error') or '',
        )
        bob_report = bob.call('POST', '/v1/caldav/sync')
        self.r.add('people', '-', "Bob's sync reports no errors", bool(bob_report.get('ok')), json.dumps(bob_report)[:200])
        bob_settings = bob.call('GET', '/v1/caldav/settings')
        found = self.phone.collections()
        bob_cal = (found.get(f'Omi – {name}') or {}).get('href') or ''
        bob_tasks = (found.get(f'Omi Tasks – {name}') or {}).get('href') or ''
        self.r.add(
            'people',
            'send',
            'each person gets their own calendar and task list on the shared account',
            bool(bob_cal and bob_tasks) and bob_cal != self.events_href and bob_tasks != self.tasks_href,
            f'alice={self.events_href} bob={bob_cal} (stored: {bob_settings.get("calendar_href")})',
        )

        def no_collision():
            alice_day = self.events().get(f'omi-day-{self.tag}-{date_str}@omi-local')
            bob_day = self.phone.objects(bob_cal, 'VEVENT').get(f'omi-day-{bob_tag}-{date_str}@omi-local')
            ok = (
                alice_day is not None
                and bob_day is not None
                and 'grandma' in unescape(alice_day.value('DESCRIPTION'))
                and 'bike' in unescape(bob_day.value('DESCRIPTION'))
                and 'grandma' not in unescape(bob_day.value('DESCRIPTION'))
            )
            return ok, f'alice: {alice_day and alice_day.href} bob: {bob_day and bob_day.href}'

        self.guard('people', 'send', 'same-date day summaries of two people do not overwrite each other', no_collision)
        self.guard(
            'people',
            'send',
            "Bob's approved event is on his calendar (no gateway)",
            lambda: (
                (obj := self.phone.objects(bob_cal, 'VEVENT').get(f'omi-event-{bob_tag}-{cid}-0@omi-local')) is not None,
                obj.href if obj else 'missing',
            ),
        )
        listing = bob.call('GET', '/v1/caldav/collections')
        alice_listed = next((c for c in listing.get('collections') or [] if c.get('href', '').rstrip('/').endswith(self.events_href.rstrip('/'))), {})
        self.r.add(
            'people',
            '-',
            "GET /v1/caldav/collections marks Alice's calendar as used by her",
            self.username in (alice_listed.get('used_by') or []),
            json.dumps(alice_listed)[:200],
        )

        # Auto-approve: off by default; the hourly job does nothing for him.
        settings = bob.call('GET', '/v1/approvals/settings')
        self.r.add('auto', '-', 'auto-approve is off by default', settings.get('auto_approve') is False, json.dumps(settings))
        outcome = seed(self.args.seed_cmd, {'op': 'auto_approve', 'uid': bob_uid})
        bob.call('POST', '/v1/caldav/sync')
        on_server = lambda: self.phone.objects(bob_tasks, 'VTODO').get(f'omi-task-{bob_tag}-{bob_task}@omi-local')  # noqa: E731
        self.r.add(
            'auto',
            'send',
            "with auto-approve off, yesterday's waiting task stays off the server",
            outcome == {} and on_server() is None,
            json.dumps(outcome),
        )
        bob.call('PUT', '/v1/approvals/settings', {'auto_approve': True, 'time': '00:00'})
        outcome = seed(self.args.seed_cmd, {'op': 'auto_approve', 'uid': bob_uid})
        bob.call('POST', '/v1/caldav/sync')
        self.r.add(
            'auto',
            'send',
            'auto-approve on, past its time: the waiting task is approved and synced',
            bob_task in (outcome.get('tasks') or []) and on_server() is not None,
            json.dumps(outcome),
        )

        # The picker: a new task list of his own, made from the app; the next sync moves there.
        made = bob.call('POST', '/v1/caldav/collections', {'name': f"Bob's list {secrets.token_hex(2)}", 'kind': 'tasks'})
        bob.call('POST', '/v1/caldav/sync')
        new_href = (made.get('collection') or {}).get('href') or ''
        self.r.add(
            'people',
            'send',
            'Create new… (POST /v1/caldav/collections) picks the new list; tasks follow, none deleted in Omi',
            bool(made.get('ok'))
            and bool(self.phone.objects(urllib.parse.urlparse(new_href).path, 'VTODO').get(f'omi-task-{bob_tag}-{bob_task}@omi-local'))
            and bob.task(bob_task) is not None,
            new_href,
        )

    def run(self) -> int:
        try:
            if self.setup():
                self.seed_day()
                self.send()
                self.receive()
                self.second_person()
        except SystemExit as stop:
            self.r.add('demo', '-', 'stopped early', False, str(stop))
        except Exception as error:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            self.r.add('demo', '-', 'crashed', False, f'{type(error).__name__}: {error}')
        finally:
            self.cleanup()
        print('\n' + self.r.table())
        failed = [r for r in self.r.rows if r.status == 'FAIL' or (self.args.strict and r.status == 'GAP')]
        return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--backend', default='http://127.0.0.1:8000', help='Omi backend URL')
    parser.add_argument('--caldav', default='http://127.0.0.1:5232/', help='CalDAV server URL (the bundled Radicale)')
    parser.add_argument('--caldav-user', default='omi')
    parser.add_argument('--caldav-password', default=os.environ.get('CALDAV_PASSWORD', ''))
    parser.add_argument('--omi-username', help='existing Omi login (default: a new one, made with --seed-cmd)')
    parser.add_argument('--omi-password')
    parser.add_argument(
        '--seed-cmd',
        help="shell command that runs python on stdin inside the backend's environment "
        "(e.g. docker exec -i -e OMI_DEMO_SEED omi-local bash -c 'cd /app/backend && PYTHONPATH=. python -')",
    )
    parser.add_argument('--allglance-url', help='ALLGlance gateway (or allglance_stub.py) for the family-calendar approve')
    parser.add_argument('--allglance-log', help='the stub\'s request log, to show what approve sent')
    parser.add_argument('--scheduler-wait', type=float, default=45.0, help='seconds to wait for the ~20 s change pass')
    parser.add_argument(
        '--full-pass-wait', type=float, default=90.0, help='seconds to wait for the full pass (CALDAV_SYNC_SECONDS)'
    )
    parser.add_argument(
        '--dirty-settle', type=float, default=25.0, help='seconds to let the change-triggered pass drain first'
    )
    parser.add_argument(
        '--isolate',
        action='store_true',
        help='sync the demo user into a calendar and task list made for this run (use on a real stack), '
        'deleted afterwards unless --keep-data',
    )
    parser.add_argument('--keep-data', action='store_true', help='with --isolate: leave the run\'s calendars')
    parser.add_argument('--strict', action='store_true', help='GAP rows fail the run too')
    args = parser.parse_args()
    if not args.caldav_password:
        parser.error('--caldav-password (or CALDAV_PASSWORD) is required')
    return Demo(args).run()


if __name__ == '__main__':
    sys.exit(main())
