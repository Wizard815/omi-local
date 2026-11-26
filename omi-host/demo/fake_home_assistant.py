#!/usr/bin/env python3
"""A stand-in Home Assistant for ha_demo.sh (not a real HA install): the REST API's documented
shapes (developers.home-assistant.io/docs/api/rest) over a small house, standard library only.

  GET  /api/                          {"message": "API running."}
  GET  /api/states                    [state objects]
  POST /api/services/<domain>/<svc>   [changed states]; turn_on/turn_off/toggle/lock/unlock... change state
  POST /api/events/<event_type>       {"message": "Event <type> fired."}
  POST /api/template                  the area list (areas() / area_entities()) as JSON text
  GET  /_demo/log                     what Omi did here: {"events": [...], "services": [...], "auth": [...]}

Every /api/ call needs ``Authorization: Bearer <token>`` (401 otherwise).

  fake_home_assistant.py --host 192.0.2.20 --port 8123 --token <long-lived token>
"""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List

AREAS = [
    ['Living Room', ['light.living_room', 'media_player.living_room_tv']],
    ['Kitchen', ['light.kitchen', 'switch.coffee_machine']],
    ['Hallway', ['climate.hallway', 'lock.front_door']],
]
ON = {'turn_on': 'on', 'turn_off': 'off', 'lock': 'locked', 'unlock': 'unlocked'}


def state(entity_id: str, value: str, name: str, **attributes: Any) -> Dict[str, Any]:
    return {
        'entity_id': entity_id,
        'state': value,
        'attributes': {'friendly_name': name, **attributes},
        'last_changed': '2026-11-25T07:30:00+00:00',
    }


class House:
    def __init__(self, token: str):
        self.token = token
        self.lock = threading.Lock()
        rows = [
            state('light.living_room', 'on', 'Living room lights', brightness=180),
            state('light.kitchen', 'off', 'Kitchen light'),
            state('switch.coffee_machine', 'off', 'Coffee machine'),
            state('media_player.living_room_tv', 'idle', 'Living room TV', volume_level=0.2),
            state('climate.hallway', 'heat', 'Hallway thermostat', current_temperature=20.5, temperature=21),
            state('lock.front_door', 'locked', 'Front door'),
            state('cover.garage_door', 'closed', 'Garage door'),
            state('sensor.energy', '412', 'Energy', unit_of_measurement='W'),
            state('person.alex', 'home', 'Alex'),
            state('person.sam', 'not_home', 'Sam'),
        ]
        self.states = {r['entity_id']: r for r in rows}
        self.log: Dict[str, List[Any]] = {'events': [], 'services': [], 'auth': []}

    def service(self, domain: str, name: str, body: Dict[str, Any]) -> List[Dict[str, Any]]:
        targets = body.get('entity_id') or []
        targets = [targets] if isinstance(targets, str) else list(targets)
        changed = []
        with self.lock:
            self.log['services'].append({'domain': domain, 'service': name, 'data': body})
            for entity_id in targets:
                row = self.states.get(entity_id)
                if row is None:
                    continue
                if name == 'toggle':
                    row['state'] = 'off' if row['state'] == 'on' else 'on'
                elif name in ON:
                    row['state'] = ON[name]
                changed.append(row)
        return changed


def handler_for(house: House):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: Any, content_type: str = 'application/json') -> None:
            data = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> Dict[str, Any]:
            length = int(self.headers.get('Content-Length') or 0)
            raw = self.rfile.read(length) if length else b''
            try:
                return json.loads(raw.decode() or '{}')
            except ValueError:
                return {}

        def _authorized(self) -> bool:
            ok = self.headers.get('Authorization') == f'Bearer {house.token}'
            house.log['auth'].append({'path': self.path, 'ok': ok})
            if not ok:
                self._send(401, {'message': 'Unauthorized'})
            return ok

        def do_GET(self) -> None:  # noqa: N802
            if self.path == '/_demo/log':
                with house.lock:
                    return self._send(200, house.log)
            if not self._authorized():
                return None
            if self.path == '/api/':
                return self._send(200, {'message': 'API running.'})
            if self.path == '/api/states':
                with house.lock:
                    return self._send(200, list(house.states.values()))
            return self._send(404, {'message': 'Not found'})

        def do_POST(self) -> None:  # noqa: N802
            body = self._body()
            if not self._authorized():
                return None
            parts = self.path.strip('/').split('/')
            if parts[:2] == ['api', 'events'] and len(parts) == 3:
                with house.lock:
                    house.log['events'].append({'event_type': parts[2], 'data': body})
                return self._send(200, {'message': f'Event {parts[2]} fired.'})
            if parts[:2] == ['api', 'services'] and len(parts) == 4:
                return self._send(200, house.service(parts[2], parts[3], body))
            if self.path == '/api/template' and 'areas()' in str(body.get('template') or ''):
                return self._send(200, json.dumps(AREAS), 'text/plain; charset=utf-8')
            return self._send(400, {'message': 'Not supported by the demo Home Assistant'})

        def log_message(self, *args: Any) -> None:  # quiet
            return

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8123)
    parser.add_argument('--token', required=True)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), handler_for(House(args.token)))
    print(f'fake Home Assistant on http://{args.host}:{args.port}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
