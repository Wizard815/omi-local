#!/usr/bin/env python3
"""ALLGlance and FAITH read Omi with their own MCP clients; neither sees unapproved items.

Runs on the stack caldav_demo.sh starts (peers_demo.sh sets OMI_DEMO_DRIVER) with:
- ALLGlance's Omi connector (gateway/src/adapters/OmiAdapter.js) from --allglance-repo;
- FAITH's generic MCP connector (server/src/connectors/mcp.ts) from --faith-server (a copy
  with node_modules), configured by omi-host/examples/faith/omi-connector.yaml with one Omi
  MCP key per person and no household key.

| area | what | checked with |
|---|---|---|
| allglance | the omi_* tools, the day summary (allglance_get_day), tasks, search | a draft day reads as nothing; waiting tasks absent; after approval the summary |
| faith | the household view, each person's view | no Omi tools without a person; each person reads only their own Omi |
| faith | a task and an event FAITH creates for a person | marked write in FAITH; waiting for that person's OK in Omi |
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from caldav_demo import Omi, Results, seed  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))


class PeersDemo:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.r = Results()
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.day = (self.now - timedelta(days=1)).date()
        self.people: Dict[str, Dict[str, Any]] = {}

    def check(self, area: str, direction: str, name: str, fn: Callable[[], Tuple[bool, str]]) -> bool:
        try:
            ok, detail = fn()
        except Exception as error:  # noqa: BLE001
            ok, detail = False, f'{type(error).__name__}: {error}'
        return self.r.add(area, direction, name, ok, detail)

    @staticmethod
    def last_json(proc: subprocess.CompletedProcess) -> Dict[str, Any]:
        lines = [line for line in proc.stdout.splitlines() if line.startswith('{')]
        if proc.returncode or not lines:
            raise RuntimeError(f'probe failed ({proc.returncode}): {(proc.stderr or proc.stdout)[-1500:]}')
        return json.loads(lines[-1])

    def person(self, name: str) -> Dict[str, Any]:
        username, password = f'{name}-{secrets.token_hex(3)}', secrets.token_urlsafe(12)
        uid = seed(self.args.seed_cmd, {'op': 'account', 'username': username, 'password': password})['uid']
        omi = Omi(self.args.backend)
        omi.login(username, password)
        key = omi.call('POST', '/v1/mcp/keys', {'name': f'{name} via FAITH'})['key']
        started = datetime.combine(self.day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=15)
        plan = [{'title': f'{name.title()} talks to the plumber', 'overview': f'{name} and Gary plan the sink repair.',
                 'started_at': started.isoformat(), 'transcript': ['Gary the plumber comes Thursday at nine.']}]
        (cid,) = seed(self.args.seed_cmd, {'op': 'conversations', 'uid': uid, 'conversations': plan})['ids']
        seed(self.args.seed_cmd, {'op': 'day_draft', 'uid': uid, 'date': self.day.isoformat(),
                                  'title': f"{name.title()}'s plumber day", 'rolled_up': [cid],
                                  'sections': [{'title': 'Plumber', 'text': f'{name.title()} booked Gary for Thursday.',
                                                'conversation_ids': [cid]}]})
        waiting, approved = seed(self.args.seed_cmd, {'op': 'tasks', 'uid': uid, 'tasks': [
            {'description': f'{name.title()}: heard, not approved yet', 'conversation_id': cid},
            {'description': f'{name.title()}: buy a sink trap', 'conversation_id': cid},
        ]})['ids']
        omi.call('POST', f'/v1/approvals/tasks/{approved}/approve', {})
        return {'uid': uid, 'omi': omi, 'key': key, 'conversation': cid, 'waiting': waiting, 'approved': approved}

    def allglance(self, who: Dict[str, Any]) -> Dict[str, Any]:
        return self.last_json(subprocess.run(
            ['node', os.path.join(HERE, 'allglance_omi_probe.mjs'), self.args.allglance_repo, self.args.backend,
             who['key'], self.day.isoformat()],
            capture_output=True, text=True, timeout=120, env={**os.environ, 'NO_PROXY': '*'}))

    def faith(self, calls: list) -> Dict[str, Any]:
        with tempfile.TemporaryDirectory() as tmp:
            text = open(os.path.join(REPO, 'omi-host/examples/faith/omi-connector.yaml')).read()
            text = text.replace('url: http://<omi-host>:8000/v1/mcp', f"url: {self.args.backend.rstrip('/')}/v1/mcp")
            with open(os.path.join(tmp, 'omi.yaml'), 'w') as handle:
                handle.write(text)
            with open(os.path.join(tmp, 'calls.json'), 'w') as handle:
                json.dump(calls, handle)
            env = {**os.environ, 'NO_PROXY': '*', 'OMI_MCP_KEY_WIZARD': self.people['wizard']['key'],
                   'OMI_MCP_KEY_PARTNER': self.people['partner']['key']}
            env.pop('OMI_MCP_KEY_HOUSEHOLD', None)
            tsx = os.path.join(self.args.faith_server, 'node_modules/.bin/tsx')
            if not os.path.exists(tsx):  # an npm workspace install hoists it to the repo root
                tsx = os.path.join(self.args.faith_server, '..', 'node_modules/.bin/tsx')
            return self.last_json(subprocess.run(
                [tsx, os.path.join(HERE, 'faith_omi_probe.mts'),
                 os.path.join(self.args.faith_server, 'src'), os.path.join(tmp, 'omi.yaml'), os.path.join(tmp, 'calls.json')],
                cwd=self.args.faith_server, capture_output=True, text=True, timeout=180, env=env))

    def run(self) -> int:
        try:
            self.people = {'wizard': self.person('wizard'), 'partner': self.person('partner')}
            self.r.add('setup', '-', 'two Omi users (wizard, partner), each with a draft day, a waiting and an approved task, '
                       'and an Omi MCP key', True)
            wiz = self.people['wizard']

            before = self.allglance(wiz)
            self.check('allglance', 'read', 'ALLGlance\'s connector lists Omi\'s core tools (omi_get_day_review ...)', lambda: (
                {'omi_get_day_review', 'omi_get_action_items', 'omi_search_conversations'} <= set(before['tools']),
                ', '.join(before['tools'])))
            self.check('allglance', 'read', 'allglance_get_day: a draft day gives no Omi summary', lambda: (
                before['day'] is None or before['day'].get('source') != 'day_review', json.dumps(before['day'])[:200]))
            items = json.dumps(before['action_items'])
            self.check('allglance', 'read', 'omi_get_action_items: the approved task, not the waiting one', lambda: (
                wiz['approved'] in items and wiz['waiting'] not in items, items[:200]))
            self.check('allglance', 'read', 'omi_search_conversations finds the plumber talk (no Typesense)', lambda: (
                wiz['conversation'] in json.dumps(before['search']), json.dumps(before['search'])[:160]))
            wiz['omi'].call('POST', f'/v1/users/day-reviews/{self.day.isoformat()}/approve')
            after = self.allglance(wiz)
            self.check('allglance', 'read', 'after the user approves the day: the approved summary', lambda: (
                (after['day'] or {}).get('source') == 'day_review' and 'booked Gary' in (after['day'] or {}).get('overview', ''),
                json.dumps(after['day'])[:200]))

            start = (self.now + timedelta(days=3)).replace(hour=16, minute=0, second=0)
            out = self.faith([
                {'view': 'wizard', 'tool': 'omi_get_action_items', 'args': {}},
                {'view': 'partner', 'tool': 'omi_get_action_items', 'args': {}},
                {'view': 'wizard', 'tool': 'omi_create_action_item', 'args': {'description': 'Wizard: call the school'}},
                {'view': 'wizard', 'tool': 'omi_create_calendar_event',
                 'args': {'title': 'Parent evening', 'start': start.isoformat(), 'requested_by': 'FAITH'}},
                {'view': 'household', 'tool': 'omi_get_action_items', 'args': {}},
            ])
            self.check('faith', 'read', 'no household key: FAITH\'s household view gets no Omi tools', lambda: (
                out['tools']['household'] == [], json.dumps(out['tools']['household'])))
            self.check('faith', 'read', 'each person\'s view gets the omi_* tools of their own key', lambda: (
                'omi_get_day_review' in out['tools']['wizard'] and out['tools']['wizard'] == out['tools']['partner'],
                f"{len(out['tools']['wizard'])} tools"))
            self.check('faith', 'read', 'FAITH treats Omi\'s create/complete tools as writes (its own approvals apply)', lambda: (
                {'omi_create_action_item', 'omi_create_calendar_event', 'omi_complete_action_item'} <= set(out['writes']['wizard'])
                and 'omi_get_action_items' not in out['writes']['wizard'], json.dumps(out['writes']['wizard'])))
            calls = out['calls']
            wiz_items, partner_items = json.dumps(calls[0]['result']), json.dumps(calls[1]['result'])
            partner = self.people['partner']
            self.check('faith', 'read', 'each person reads only their own Omi, never a waiting task', lambda: (
                wiz['approved'] in wiz_items and partner['approved'] not in wiz_items and partner['approved'] in partner_items
                and wiz['waiting'] not in wiz_items, f'wizard={wiz_items[:100]} partner={partner_items[:100]}'))
            created = calls[2]['result'] if isinstance(calls[2]['result'], dict) else json.loads(calls[2]['result'])
            event = calls[3]['result'] if isinstance(calls[3]['result'], dict) else json.loads(calls[3]['result'])
            waiting = wiz['omi'].call('GET', '/v1/approvals')
            self.check('faith', 'send', 'a task FAITH creates lands in the person\'s Waiting for your OK', lambda: (
                (created.get('action_item') or {}).get('approval') == 'pending'
                and (created.get('action_item') or {}).get('id') in [t.get('id') for t in waiting.get('tasks') or []],
                json.dumps(created)[:200]))
            self.check('faith', 'send', 'an event FAITH proposes waits in the person\'s Calendar check', lambda: (
                (event.get('event') or {}).get('status') == 'waiting_for_approval'
                and (event.get('event') or {}).get('conversation_id') in [e.get('conversation_id') for e in waiting.get('events') or []],
                json.dumps(event)[:200]))
            self.check('faith', 'read', 'the household call is refused (not offered in that view)', lambda: (
                calls[4]['result'] == {'error': 'not offered in this view'}, json.dumps(calls[4]['result'])))
        except Exception as error:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            self.r.add('demo', '-', 'crashed', False, f'{type(error).__name__}: {error}')
        print('\n' + self.r.table())
        return 1 if any(r.status == 'FAIL' for r in self.r.rows) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--backend', default='http://127.0.0.1:8000')
    parser.add_argument('--seed-cmd', required=True)
    parser.add_argument('--allglance-repo', default=os.environ.get('OMI_ALLGLANCE_REPO', ''))
    parser.add_argument('--faith-server', default=os.environ.get('OMI_FAITH_SERVER', ''))
    args, _unknown = parser.parse_known_args()
    if not (args.allglance_repo and args.faith_server):
        parser.error('--allglance-repo and --faith-server are required')
    return PeersDemo(args).run()


if __name__ == '__main__':
    sys.exit(main())
