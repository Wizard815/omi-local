#!/usr/bin/env python3
"""Hermes demo: Hermes uses Omi as a source and an actuator over MCP, Omi hands work to
Hermes, and the user's approval holds everywhere. Prints a PASS/FAIL table; exits 1 on FAIL.

Runs on the stack caldav_demo.sh starts (hermes_demo.sh sets OMI_DEMO_DRIVER), with:

- hermes-agent's own MCP client (``--hermes-python`` / ``--hermes-src``: a hermes-agent
  install) driven by hermes_mcp_probe.py: the ``mcp_servers.omi`` entry of Hermes'
  config.yaml (url + Authorization: Bearer <Omi MCP key>, Streamable HTTP), its tools
  called through Hermes' tool dispatcher;
- the phone as raw CalDAV (DAVx5-style), against the bundled Radicale;
- optionally a real hermes-agent API server (``--hermes-url``/``--hermes-key``) for
  Send to agent: POST /v1/runs, the run's approval relayed through Omi's Waiting for your
  OK, the result attached to the conversation.

| area | what | checked with |
|---|---|---|
| mcp | Hermes connects with an Omi MCP key and registers Omi's tools | the mcp__omi__* tools Hermes lists |
| mcp | search_conversations, get_day_review before the day is approved | the conversation found; no draft text |
| mcp | the same after Approve: the approved summary, and the rolled-up conversation still found | review text; search hit |
| tasks | create_action_item from Hermes | waits (approval=pending), not on the CalDAV list |
| tasks | the user approves it in Omi | VTODO on the user's CalDAV list |
| tasks | the phone completes it (STATUS:COMPLETED) | Omi marks it done; Hermes sees it done |
| tasks | Hermes completes an approved task | VTODO STATUS:COMPLETED |
| events | create_calendar_event from Hermes | Waiting for your OK; no VEVENT; get_calendar_events says waiting |
| events | the user approves it | VEVENT on the user's calendar; get_calendar_events says on_calendar |
| memory | create_memory / search_memories from Hermes | found |
| search | POST /v1/conversations/search with no Typesense | 200 with the conversation |
| agent | Send to agent -> a real Hermes /v1/runs; its approval in Omi; Allow once | run completes; result on the conversation |
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from caldav_demo import HttpError, Omi, Phone, Results, ical_utc, owner_tag, seed  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


class HermesDemo:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.omi = Omi(args.backend)
        self.phone = Phone(args.caldav, args.caldav_user, args.caldav_password)
        self.r = Results()
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.day = (self.now - timedelta(days=1)).date()
        self.uid = ''
        self.tag = ''
        self.username = ''
        self.mcp_key = ''
        self.tasks_href = ''
        self.events_href = ''
        self.ids: Dict[str, str] = {}

    # ---- helpers -----------------------------------------------------------------------------
    def check(self, area: str, direction: str, name: str, fn: Callable[[], Tuple[bool, str]]) -> bool:
        try:
            ok, detail = fn()
        except Exception as error:  # noqa: BLE001 — one broken step is one FAIL row
            ok, detail = False, f'{type(error).__name__}: {error}'
        return self.r.add(area, direction, name, ok, detail)

    def wait_for(self, predicate: Callable[[], Any], seconds: float, step: float = 2.0) -> Any:
        end = time.monotonic() + seconds
        while True:
            value = predicate()
            if value or time.monotonic() > end:
                return value
            time.sleep(step)

    def sync(self) -> Dict[str, Any]:
        return self.omi.call('POST', '/v1/caldav/sync')

    def todo(self, task_id: str):
        return self.phone.objects(self.tasks_href, 'VTODO').get(f'omi-task-{self.tag}-{task_id}@omi-local')

    def event(self, conversation_id: str, idx: int = 0):
        return self.phone.objects(self.events_href, 'VEVENT').get(
            f'omi-event-{self.tag}-{conversation_id}-{idx}@omi-local'
        )

    def hermes(self, calls: List[Dict[str, Any]]) -> Dict[str, Any]:
        """One session of Hermes' MCP client: connect, list, make these calls."""
        with tempfile.TemporaryDirectory(prefix='hermes-home-') as home, tempfile.NamedTemporaryFile(
            'w', suffix='.json', delete=False
        ) as handle:
            json.dump(calls, handle)
            handle.flush()
            env = {**os.environ, 'HERMES_HOME': home, 'NO_PROXY': '*', 'no_proxy': '*'}
            proc = subprocess.run(
                [
                    self.args.hermes_python,
                    os.path.join(HERE, 'hermes_mcp_probe.py'),
                    '--url',
                    self.args.backend.rstrip('/') + '/v1/mcp',
                    '--key',
                    self.mcp_key,
                    '--calls',
                    handle.name,
                ],
                cwd=self.args.hermes_src,
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
            )
        lines = [line for line in proc.stdout.splitlines() if line.startswith('{')]
        if proc.returncode or not lines:
            raise RuntimeError(f'hermes probe failed ({proc.returncode}): {proc.stderr[-1500:]}')
        out = json.loads(lines[-1])
        out['by_tool'] = {}
        for item in out['results']:
            out['by_tool'].setdefault(item['tool'], []).append(item['result'])
        return out

    @staticmethod
    def payload(result: Any) -> Any:
        """What Hermes hands its model: ``{"result": "<the tool's JSON text>"}``, decoded."""
        if isinstance(result, dict) and isinstance(result.get('result'), str):
            try:
                return json.loads(result['result'])
            except ValueError:
                return result['result']
        if isinstance(result, dict) and isinstance(result.get('result'), dict):
            return result['result']
        return result

    # ---- setup -------------------------------------------------------------------------------
    def setup(self) -> bool:
        self.username = f'hermes-demo-{secrets.token_hex(3)}'
        password = secrets.token_urlsafe(12)
        self.uid = seed(self.args.seed_cmd, {'op': 'account', 'username': self.username, 'password': password})['uid']
        self.omi.login(self.username, password)
        self.tag = owner_tag(self.uid)
        self.r.add('setup', '-', f'local login as {self.username}', True)
        test = self.omi.call('POST', '/v1/caldav/test')
        self.r.add('setup', '-', 'CalDAV reaches the bundled Radicale', bool(test.get('ok')), test.get('error') or '')
        created = self.omi.call('POST', '/v1/mcp/keys', {'name': 'Hermes'})
        self.mcp_key = created['key']
        self.r.add('setup', '-', 'an Omi MCP key for Hermes (POST /v1/mcp/keys)', self.mcp_key.startswith('omi_mcp_'),
                   f"scopes={created.get('scopes')}")

        started = datetime.combine(self.day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=14)
        plan = [
            {
                'title': 'Call with Gary the plumber',
                'overview': 'Gary comes Thursday at nine to fix the kitchen sink.',
                'started_at': started.isoformat(),
                'transcript': [
                    'Gary, I promise I will clear out everything under the kitchen sink before Thursday.',
                    'Great, and leave the side gate unlocked so I can come in the back.',
                    'Will do, the gate will be open from eight.',
                ],
            },
            {
                'title': 'School pickup plan',
                'overview': 'Mia gets picked up at three by grandma.',
                'started_at': (started + timedelta(hours=2)).isoformat(),
                'transcript': ['Grandma picks Mia up at three.', 'OK, I will tell the school office.'],
            },
        ]
        ids = seed(self.args.seed_cmd, {'op': 'conversations', 'uid': self.uid, 'conversations': plan})['ids']
        self.ids['gary'], self.ids['school'] = ids
        seed(
            self.args.seed_cmd,
            {
                'op': 'day_draft',
                'uid': self.uid,
                'date': self.day.isoformat(),
                'title': 'Plumber prep and school pickup',
                'rolled_up': ids,
                'sections': [
                    {'title': 'The plumber', 'text': 'You promised Gary to clear under the sink and leave the gate open.',
                     'conversation_ids': [ids[0]]},
                    {'title': 'School', 'text': 'Grandma picks Mia up at three.', 'conversation_ids': [ids[1]]},
                ],
            },
        )
        self.r.add('setup', '-', f'seeded 2 conversations on {self.day} and their day summary draft', True)
        report = self.sync()
        found = self.phone.collections()
        self.tasks_href = (found.get(f'Omi Tasks – {self.username}') or {}).get('href') or ''
        self.events_href = (found.get(f'Omi – {self.username}') or {}).get('href') or ''
        self.r.add('setup', '-', 'the first sync made the user\'s own CalDAV lists', bool(self.tasks_href and self.events_href),
                   json.dumps({'ok': report.get('ok'), 'tasks': self.tasks_href, 'events': self.events_href}))
        return bool(self.tasks_href and self.events_href and self.mcp_key)

    # ---- Hermes reads Omi --------------------------------------------------------------------
    def read(self) -> None:
        date = self.day.isoformat()
        before = self.hermes(
            [
                {'tool': 'get_day_review', 'args': {'date': date}},
                {'tool': 'search_conversations', 'args': {'query': 'Gary plumber sink'}},
            ]
        )
        wanted = {'mcp__omi__search_conversations', 'mcp__omi__get_day_review', 'mcp__omi__get_action_items',
                  'mcp__omi__create_action_item', 'mcp__omi__complete_action_item', 'mcp__omi__get_calendar_events',
                  'mcp__omi__create_calendar_event', 'mcp__omi__get_memories', 'mcp__omi__create_memory'}
        self.check('mcp', 'read', 'Hermes\' MCP client connects with the Omi key and registers Omi\'s tools', lambda: (
            wanted <= set(before['tools']), f"{len(before['tools'])} tools; missing {sorted(wanted - set(before['tools']))}"))
        review = self.payload(before['by_tool']['get_day_review'][0])
        self.check('mcp', 'read', 'get_day_review on a draft day: no summary text (drafts never leave Omi)', lambda: (
            isinstance(review, dict) and review.get('review') is None and 'promised Gary' not in json.dumps(review),
            json.dumps({k: review.get(k) for k in ('bundled', 'review')} if isinstance(review, dict) else review)[:300]))
        hits = self.payload(before['by_tool']['search_conversations'][0])
        self.check('mcp', 'read', 'search_conversations finds the plumber call (no Typesense: Firestore text search)', lambda: (
            self.ids['gary'] in json.dumps(hits), json.dumps(hits)[:300]))
        rest = self.omi.call('POST', '/v1/conversations/search', {'query': 'plumber', 'per_page': 10})
        self.check('search', 'read', 'POST /v1/conversations/search answers 200 without Typesense', lambda: (
            self.ids['gary'] in [c.get('id') for c in rest.get('items') or []], f"{len(rest.get('items') or [])} items"))

        approved = self.omi.call('POST', f'/v1/users/day-reviews/{date}/approve')
        self.r.add('mcp', '-', 'the user approves the day in Omi', approved.get('draft') is False, f"draft={approved.get('draft')}")
        after = self.hermes(
            [
                {'tool': 'get_day_review', 'args': {'date': date}},
                {'tool': 'search_conversations', 'args': {'query': 'what did I promise Gary'}},
            ]
        )
        review = self.payload(after['by_tool']['get_day_review'][0])
        self.check('mcp', 'read', 'get_day_review after approval: the approved summary', lambda: (
            'promised Gary' in json.dumps(review), json.dumps((review or {}).get('review') or {})[:300]))
        hits = self.payload(after['by_tool']['search_conversations'][0])
        self.check('mcp', 'read', '"what did I promise Gary": the conversation rolled into the approved day is still found', lambda: (
            self.ids['gary'] in json.dumps(hits), json.dumps(hits)[:300]))

    # ---- Hermes acts through Omi: tasks, events, memories ------------------------------------
    def act(self) -> None:
        due = (self.now + timedelta(days=1)).replace(hour=15, minute=0, second=0)
        start = (self.now + timedelta(days=2)).replace(hour=21, minute=0, second=0)
        out = self.hermes(
            [
                {'tool': 'create_action_item', 'args': {'description': 'Call State Farm about the multi-car discount',
                                                        'due_at': due.isoformat()}},
                {'tool': 'create_action_item', 'args': {'description': 'Buy a new sink trap'}},
                {'tool': 'create_calendar_event', 'args': {'title': "Theo's dentist", 'start': start.isoformat(),
                                                           'duration': 45, 'requested_by': 'Hermes'}},
                {'tool': 'create_memory', 'args': {'content': 'The family plumber is Gary; he prefers the back gate.'}},
            ]
        )
        task = (self.payload(out['by_tool']['create_action_item'][0]) or {}).get('action_item') or {}
        trap = (self.payload(out['by_tool']['create_action_item'][1]) or {}).get('action_item') or {}
        event = (self.payload(out['by_tool']['create_calendar_event'][0]) or {}).get('event') or {}
        self.ids.update(task=task.get('id', ''), trap=trap.get('id', ''), event=event.get('conversation_id', ''))
        self.check('tasks', 'send', 'create_action_item from Hermes waits for the user\'s OK', lambda: (
            task.get('approval') == 'pending' and trap.get('approval') == 'pending', json.dumps(task)[:300]))
        self.check('events', 'send', 'create_calendar_event from Hermes waits for the user\'s OK', lambda: (
            event.get('status') == 'waiting_for_approval', json.dumps(event)[:300]))
        waiting = self.omi.call('GET', '/v1/approvals')
        self.check('tasks', 'send', 'Waiting for your OK (GET /v1/approvals) lists both tasks and the event', lambda: (
            {self.ids['task'], self.ids['trap']} <= {t.get('id') for t in waiting.get('tasks') or []}
            and (waiting.get('event_count') or 0) >= 1,
            f"tasks={[t.get('description') for t in waiting.get('tasks') or []]} events={waiting.get('event_count')}"))
        self.sync()
        self.check('tasks', 'send', 'nothing Hermes asked for is on CalDAV before approval', lambda: (
            self.todo(self.ids['task']) is None and self.event(self.ids['event']) is None, ''))

        approved = self.omi.call('POST', f"/v1/approvals/tasks/{self.ids['task']}/approve", {})
        self.omi.call('POST', f"/v1/approvals/tasks/{self.ids['trap']}/approve", {})
        self.omi.call('POST', '/v1/family-calendar/events', {'conversation_id': self.ids['event'], 'event_idx': 0})
        self.sync()
        todo = self.todo(self.ids['task'])
        self.check('tasks', 'send', 'approved in Omi -> the VTODO is on the user\'s CalDAV list', lambda: (
            todo is not None and 'State Farm' in todo.value('SUMMARY'), todo.value('SUMMARY') if todo else 'absent'))
        vevent = self.event(self.ids['event'])
        self.check('events', 'send', 'approved in Omi -> the VEVENT is on the user\'s calendar', lambda: (
            vevent is not None and 'dentist' in vevent.value('SUMMARY'), vevent.value('SUMMARY') if vevent else 'absent'))
        self.r.add('tasks', '-', 'POST /v1/approvals/tasks/{id}/approve answers ok', bool(approved.get('ok')),
                   json.dumps(approved)[:200])

        # The phone (DAVx5 + Tasks.org) ticks the task off.
        self.phone.edit(todo, {'STATUS': 'COMPLETED', 'PERCENT-COMPLETE': '100', 'COMPLETED': ical_utc(datetime.now(timezone.utc))})
        self.sync()
        omi_task = self.omi.task(self.ids['task']) or {}
        self.check('tasks', 'receive', 'completed on the phone (VTODO STATUS:COMPLETED) -> Omi marks it done', lambda: (
            omi_task.get('completed') is True, json.dumps({k: omi_task.get(k) for k in ('completed', 'description')})))

        later = self.hermes(
            [
                {'tool': 'get_action_items', 'args': {'limit': 50}},
                {'tool': 'complete_action_item', 'args': {'action_item_id': self.ids['trap']}},
                {'tool': 'get_calendar_events', 'args': {'start_date': self.now.date().isoformat(),
                                                         'end_date': (self.now + timedelta(days=5)).date().isoformat()}},
                {'tool': 'search_memories', 'args': {'query': 'plumber Gary back gate'}},
                {'tool': 'get_memories', 'args': {'limit': 20}},
            ]
        )
        items = json.dumps(self.payload(later['by_tool']['get_action_items'][0]))
        self.check('tasks', 'receive', 'Hermes (get_action_items) sees the phone\'s completion', lambda: (
            self.ids['task'] in items and '"completed": true' in items.split(self.ids['task'])[1][:400], items[:300]))
        self.sync()
        trap_todo = self.todo(self.ids['trap'])
        self.check('tasks', 'send', 'Hermes completes an approved task -> VTODO STATUS:COMPLETED', lambda: (
            trap_todo is not None and trap_todo.value('STATUS') == 'COMPLETED', trap_todo.value('STATUS') if trap_todo else 'absent'))
        events = self.payload(later['by_tool']['get_calendar_events'][0]) or {}
        mine = [e for e in events.get('events') or [] if e.get('conversation_id') == self.ids['event']]
        self.check('events', 'receive', 'get_calendar_events: the approved event is on_calendar', lambda: (
            bool(mine) and mine[0].get('status') == 'on_calendar', json.dumps(mine)[:300]))
        created = self.payload(out['by_tool']['create_memory'][0]) or {}
        self.check('memory', 'send', 'create_memory from Hermes is accepted', lambda: (
            isinstance(created, dict) and 'error' not in created, json.dumps(created)[:300]))
        memories = json.dumps(self.payload(later['by_tool']['search_memories'][0])) + json.dumps(
            self.payload(later['by_tool']['get_memories'][0]))
        if 'back gate' in memories:
            self.r.add('memory', 'read', 'Hermes reads the memory back', True, memories[:200])
        else:
            # Memory intake files a memory through the model (canonical promotion); this stack
            # has none, so intake defers it to the Dream retry. Not a pass, not a failure here.
            self.r.info('memory', 'read', 'Hermes reads the memory back',
                        'deferred: memory intake needs a model (offline stack); readable once Dream files it')

    # ---- Omi hands work to a real Hermes -----------------------------------------------------
    def agent(self) -> None:
        if not self.args.hermes_url:
            self.r.info('agent', 'send', 'Send to agent against a real Hermes', 'skipped: no --hermes-url')
            return
        self.omi.call('PUT', '/v1/integrations/outbound', {
            'hermes': {'enabled': True, 'url': self.args.hermes_url, 'token': self.args.hermes_key}})
        probe = f'/tmp/omi-hermes-demo-{secrets.token_hex(3)}'
        request = {
            'target': 'hermes',
            'prompt': f'Use the terminal tool to run exactly: rm -rf {probe} . Then reply with one short line.',
            'conversation_ids': [self.ids['school']],
            'attach_result': True,
        }
        job = self.omi.call('POST', '/v1/agent-jobs', request)['job']

        def waiting():
            current = self.omi.call('GET', f"/v1/agent-jobs/{job['id']}")
            current = current.get('job', current)
            return current if current.get('status') in ('waiting_for_approval', 'failed', 'completed') else None

        asked = self.wait_for(waiting, self.args.hermes_wait, 3) or {}
        question = asked.get('approval') or {}
        self.check('agent', 'approve', 'a real Hermes run\'s approval shows in Omi with its command', lambda: (
            asked.get('status') == 'waiting_for_approval' and probe in str(question.get('command') or ''),
            json.dumps({'status': asked.get('status'), 'approval': question, 'error': asked.get('error')})[:300]))
        if asked.get('status') != 'waiting_for_approval':
            return
        self.omi.call('POST', f"/v1/agent-jobs/{job['id']}/approval", {'choice': 'once'})

        def done():
            current = self.omi.call('GET', f"/v1/agent-jobs/{job['id']}")
            current = current.get('job', current)
            return current if current.get('status') in ('completed', 'failed', 'cancelled') else None

        final = self.wait_for(done, self.args.hermes_wait, 3) or {}
        self.check('agent', 'result', 'Allow once relayed; the Hermes run completes with a result', lambda: (
            final.get('status') == 'completed' and bool(final.get('result')),
            json.dumps({k: final.get(k) for k in ('status', 'result', 'error')})[:300]))
        conversation = self.omi.call('GET', f"/v1/conversations/{self.ids['school']}")
        results = json.dumps(conversation.get('apps_results') or [])
        self.check('agent', 'result', 'the result is attached to the conversation it was sent from', lambda: (
            'agent-job-hermes' in results, results[:300]))

    def run(self) -> int:
        try:
            if self.setup():
                self.read()
                self.act()
                self.agent()
        except Exception as error:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            self.r.add('demo', '-', 'crashed', False, f'{type(error).__name__}: {error}')
        print('\n' + self.r.table())
        return 1 if any(r.status == 'FAIL' for r in self.r.rows) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--backend', default='http://127.0.0.1:8000')
    parser.add_argument('--caldav', default='http://127.0.0.1:5232/')
    parser.add_argument('--caldav-user', default='omi')
    parser.add_argument('--caldav-password', default=os.environ.get('CALDAV_PASSWORD', ''))
    parser.add_argument('--seed-cmd', required=True)
    parser.add_argument('--hermes-python', default=os.environ.get('OMI_HERMES_PYTHON', ''))
    parser.add_argument('--hermes-src', default=os.environ.get('OMI_HERMES_SRC', ''))
    parser.add_argument('--hermes-url', default=os.environ.get('OMI_HERMES_URL', ''))
    parser.add_argument('--hermes-key', default=os.environ.get('OMI_HERMES_KEY', ''))
    parser.add_argument('--hermes-wait', type=float, default=240.0)
    args, _unknown = parser.parse_known_args()  # caldav_demo.sh passes its own driver's flags too
    if not (args.hermes_python and args.hermes_src):
        parser.error('--hermes-python and --hermes-src (a hermes-agent install) are required')
    return HermesDemo(args).run()


if __name__ == '__main__':
    sys.exit(main())
