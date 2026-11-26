#!/usr/bin/env python3
"""Home Assistant demo: Omi fires events into Home Assistant, an agent reads the house over MCP,
a light call waits for the user's OK and then runs, and a lock call is refused. Prints a
PASS/FAIL table; exits 1 on any FAIL.

Runs on the stack ha_demo.sh starts: the real backend (OMI_HA_URL / OMI_HA_TOKEN pointing at
fake_home_assistant.py on a LAN address allowed by OMI_WEBHOOK_ALLOWED_PRIVATE_NETWORKS), the
Firestore and Auth emulators and redis.

| area | what | checked with |
|---|---|---|
| setup | settings come from the stack; Test connection | GET /v1/home-assistant/settings (no token), POST /test |
| events | a seeded conversation finishes (the pipeline's post-processing hook) | omi_conversation_finished + omi_task_created on the fake HA: title, short overview, person, no transcript |
| read | an agent reads the house over MCP (ha_get_states, ha_whos_home, ha_area_summary) | only allowed entities; no lock/cover/sensor |
| act | ha_call_service light.turn_off over MCP | waiting_for_approval; nothing reached HA; omi_waiting_for_approval fired with the count |
| act | the user approves it (POST /v1/home-assistant/actions/{id}/approve) | HA got light/turn_off; the light is off; the log says executed by user |
| act | ha_call_service lock.unlock | refused; HA never got a lock call; logged as refused |
| token | every body Omi answered | the token appears in none |
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
import urllib.request
from typing import Any, Callable, Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from caldav_demo import Omi, Results, seed  # noqa: E402


class HaDemo:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.omi = Omi(args.backend)
        self.r = Results()
        self.uid = ''
        self.mcp_key = ''
        self.bodies: List[str] = []

    def check(self, area: str, name: str, fn: Callable[[], Tuple[bool, str]]) -> bool:
        try:
            ok, detail = fn()
        except Exception as error:  # noqa: BLE001 — one broken step is one FAIL row
            ok, detail = False, f'{type(error).__name__}: {error}'
        return self.r.add(area, '-', name, ok, detail)

    def call(self, method: str, path: str, payload: Any = None) -> Any:
        out = self.omi.call(method, path, payload)
        self.bodies.append(json.dumps(out, default=str))
        return out

    def ha_log(self) -> Dict[str, Any]:
        with urllib.request.urlopen(self.args.ha_url.rstrip('/') + '/_demo/log', timeout=5) as response:
            return json.loads(response.read())

    def events(self, name: str) -> List[Dict[str, Any]]:
        return [e['data'] for e in self.ha_log()['events'] if e['event_type'] == name]

    def wait_for(self, predicate: Callable[[], Any], seconds: float = 20.0) -> Any:
        end = time.monotonic() + seconds
        while True:
            value = predicate()
            if value or time.monotonic() > end:
                return value
            time.sleep(0.5)

    def mcp(self, tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps(
            {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': tool, 'arguments': arguments}}
        ).encode()
        request = urllib.request.Request(
            self.args.backend.rstrip('/') + '/v1/mcp',
            data=body,
            method='POST',
            headers={
                'Authorization': f'Bearer {self.mcp_key}',
                'Content-Type': 'application/json',
                'Accept': 'application/json, text/event-stream',
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode()
        self.bodies.append(text)
        if text.lstrip().startswith('{'):
            answer = json.loads(text)
        else:
            data = [line[5:].strip() for line in text.splitlines() if line.startswith('data:')]
            answer = json.loads(data[-1])
        result = answer.get('result') or {}
        if result.get('isError'):
            raise RuntimeError(json.dumps(result.get('structuredContent') or result)[:300])
        return result.get('structuredContent') or json.loads(result['content'][0]['text'])

    # ---- steps ---------------------------------------------------------------------------------
    def setup(self) -> bool:
        username, password = f'ha-demo-{secrets.token_hex(3)}', secrets.token_urlsafe(12)
        self.uid = seed(self.args.seed_cmd, {'op': 'account', 'username': username, 'password': password})['uid']
        self.omi.login(username, password)
        self.r.add('setup', '-', f'local login as {username}', True)
        settings = self.call('GET', '/v1/home-assistant/settings')
        self.r.add(
            'setup',
            '-',
            'Home Assistant comes from the stack (OMI_HA_URL + OMI_HA_TOKEN); the token is not returned',
            bool(settings.get('configured') and settings.get('url_from_env') and settings.get('has_token'))
            and 'token' not in settings,
            json.dumps({k: settings.get(k) for k in ('url', 'has_token', 'read_allow', 'action_domains')}),
        )
        test = self.call('POST', '/v1/home-assistant/test')
        self.r.add('setup', '-', 'Test connection reaches Home Assistant with the token', bool(test.get('ok')), test.get('detail') or test.get('error') or '')
        chosen = self.call(
            'PUT',
            '/v1/home-assistant/settings',
            {
                'person': 'Alex',
                'events': {'conversation_finished': True, 'task_created': True, 'waiting_for_approval': True},
            },
        )
        self.r.add('setup', '-', 'the user turns three events on (PUT /v1/home-assistant/settings)', chosen['events']['waiting_for_approval'], json.dumps(chosen['events']))
        self.mcp_key = self.call('POST', '/v1/mcp/keys', {'name': 'Home agent'})['key']
        self.r.add('setup', '-', 'an Omi MCP key for an agent (POST /v1/mcp/keys)', self.mcp_key.startswith('omi_mcp_'))
        return bool(settings.get('configured') and test.get('ok'))

    def conversation_events(self) -> None:
        started = time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime(time.time() - 3600))
        spec = {
            'op': 'conversations',
            'uid': self.uid,
            'conversations': [
                {
                    'title': 'Garage shelves with Sam',
                    'overview': 'Planned three shelves for the garage and who buys the boards.',
                    'started_at': started,
                    'minutes': 12,
                    'transcript': ['private words that never leave Omi', 'we need three boards'],
                    'action_items': [{'description': 'Buy three boards at the hardware store'}],
                }
            ],
        }
        cid = seed(self.args.seed_cmd, spec)['ids'][0]
        seed(self.args.seed_cmd, {'op': 'conversation_finished', 'uid': self.uid, 'conversation_id': cid})

        def finished() -> Tuple[bool, str]:
            rows = [e for e in self.events('omi_conversation_finished') if e.get('conversation_id') == cid]
            row = rows[-1] if rows else {}
            ok = row.get('title') == 'Garage shelves with Sam' and row.get('person') == 'Alex' and row.get('task_count') == 1
            ok = ok and 'private words' not in json.dumps(row) and 'transcript' not in row
            return ok, json.dumps(row)

        def task() -> Tuple[bool, str]:
            rows = [e for e in self.events('omi_task_created') if e.get('conversation_id') == cid]
            return bool(rows) and rows[-1].get('description', '').startswith('Buy three boards'), json.dumps(rows[-1:] )

        self.check('events', 'omi_conversation_finished: title, overview, person, no transcript', finished)
        self.check('events', 'omi_task_created for the task it produced', task)

    def reads(self) -> None:
        def states() -> Tuple[bool, str]:
            out = self.mcp('ha_get_states', {})
            ids = sorted(e['entity_id'] for e in out['entities'])
            hidden = {'lock.front_door', 'cover.garage_door', 'sensor.energy'} & set(ids)
            return 'light.living_room' in ids and not hidden, ', '.join(ids)

        def lights() -> Tuple[bool, str]:
            out = self.mcp('ha_get_states', {'domain': 'light', 'area': 'Living Room'})
            row = out['entities'][0] if out['entities'] else {}
            return row.get('entity_id') == 'light.living_room' and row.get('state') == 'on', json.dumps(out)

        def home() -> Tuple[bool, str]:
            out = self.mcp('ha_whos_home', {})
            return out['home'] == ['Alex'] and out['away'] == ['Sam'], json.dumps({'home': out['home'], 'away': out['away']})

        def areas() -> Tuple[bool, str]:
            out = self.mcp('ha_area_summary', {})
            return any(a['area'] == 'Living Room' and a.get('lights_on') for a in out['areas']), json.dumps(out)

        self.check('read', 'ha_get_states over MCP: allowed entities only (no lock, cover, sensor)', states)
        self.check('read', 'ha_get_states by domain and area: the living room light is on', lights)
        self.check('read', "ha_whos_home: Alex home, Sam away", home)
        self.check('read', 'ha_area_summary: lights on per area', areas)

    def actions(self) -> None:
        asked: Dict[str, Any] = {}

        def ask() -> Tuple[bool, str]:
            asked.update(
                self.mcp(
                    'ha_call_service',
                    {'domain': 'light', 'service': 'turn_off', 'entity_id': 'light.living_room', 'requested_by': 'Pendant'},
                )
            )
            nothing = not [s for s in self.ha_log()['services'] if s['domain'] == 'light']
            return asked.get('status') == 'waiting_for_approval' and nothing, json.dumps(asked)

        self.check('act', '"turn off the living room lights": waiting for your OK, nothing sent to HA', ask)

        def listed() -> Tuple[bool, str]:
            out = self.call('GET', '/v1/home-assistant/actions')
            row = next((a for a in out['actions'] if a['id'] == asked.get('action_id')), {})
            return row.get('status') == 'waiting_for_approval' and out['waiting_count'] >= 1, json.dumps(row)[:300]

        def flashed() -> Tuple[bool, str]:
            rows = self.wait_for(lambda: self.events('omi_waiting_for_approval'), 20)
            return bool(rows) and rows[-1].get('home_actions') == 1 and rows[-1].get('person') == 'Alex', json.dumps(rows[-1:] if rows else [])

        self.check('act', 'the action waits in GET /v1/home-assistant/actions', listed)
        self.check('act', 'omi_waiting_for_approval fired (an automation can flash a light)', flashed)

        def approve() -> Tuple[bool, str]:
            out = self.call('POST', f"/v1/home-assistant/actions/{asked['action_id']}/approve")
            sent = [s for s in self.ha_log()['services'] if s['domain'] == 'light' and s['service'] == 'turn_off']
            light = self.mcp('ha_get_states', {'domain': 'light'})
            off = any(e['entity_id'] == 'light.living_room' and e['state'] == 'off' for e in light['entities'])
            ok = out.get('ok') and out['action']['status'] == 'executed' and out['action']['decided_by'] == 'user'
            return bool(ok and sent and sent[-1]['data'] == {'entity_id': 'light.living_room'} and off), json.dumps(out['action'])[:300]

        self.check('act', 'the user approves: HA runs light.turn_off and the light is off', approve)

        def lock() -> Tuple[bool, str]:
            out = self.mcp('ha_call_service', {'domain': 'lock', 'service': 'unlock', 'entity_id': 'lock.front_door'})
            calls = [s for s in self.ha_log()['services'] if s['domain'] == 'lock']
            log = self.call('GET', '/v1/home-assistant/actions')
            row = next((a for a in log['actions'] if a['id'] == out.get('action_id')), {})
            return out.get('status') == 'refused' and not calls and row.get('status') == 'refused', out.get('error', '')

        self.check('act', 'ha_call_service lock.unlock is refused and never reaches HA', lock)

    def token(self) -> None:
        def none() -> Tuple[bool, str]:
            leaked = [b[:80] for b in self.bodies if self.args.ha_token in b]
            return not leaked, f'{len(self.bodies)} bodies checked'

        def authorized() -> Tuple[bool, str]:
            auth = self.ha_log()['auth']
            return bool(auth) and all(a['ok'] for a in auth), f'{len(auth)} HA calls, all with the token'

        self.check('token', 'the Home Assistant token is in no answer Omi gave', none)
        self.check('token', 'every call Omi made to HA carried it (server side only)', authorized)

    def run(self) -> int:
        if self.setup():
            self.conversation_events()
            self.reads()
            self.actions()
        self.token()
        print(self.r.table(), flush=True)
        return 1 if any(r.status == 'FAIL' for r in self.r.rows) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description='Home Assistant demo driver (see the header)')
    parser.add_argument('--backend', required=True)
    parser.add_argument('--seed-cmd', required=True)
    parser.add_argument('--ha-url', required=True)
    parser.add_argument('--ha-token', required=True)
    return HaDemo(parser.parse_args()).run()


if __name__ == '__main__':
    raise SystemExit(main())
