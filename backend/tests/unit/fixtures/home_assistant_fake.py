"""A fake Home Assistant for the omi-local Home Assistant tests: a loopback HTTP server
(tests/unit/fixtures/outbound_fakes.FakeServer) answering the REST API shapes Home
Assistant documents (developers.home-assistant.io/docs/api/rest): GET /api/ ->
{"message": "API running."}; GET /api/states -> [state objects]; POST
/api/services/<domain>/<service> -> [changed states]; POST /api/events/<type> ->
{"message": "Event <type> fired."}; POST /api/template -> rendered text; 401 without the
token. Areas answer the template utils/home_assistant/context.py sends.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from tests.unit.fixtures.outbound_fakes import FakeServer, Recorded

TOKEN = 'ha-test-token-0f3c9a7e5b'


def _state(entity_id: str, state: str, name: str, **attributes: Any) -> Dict[str, Any]:
    return {
        'entity_id': entity_id,
        'state': state,
        'attributes': {'friendly_name': name, **attributes},
        'last_changed': '2026-11-25T08:00:00+00:00',
    }


def house() -> Dict[str, Dict[str, Any]]:
    rows = [
        _state('light.living_room', 'on', 'Living room lights', brightness=200, entity_picture='/big.png'),
        _state('light.kitchen', 'off', 'Kitchen light'),
        _state('switch.coffee', 'off', 'Coffee machine'),
        _state('switch.garage_door', 'off', 'Garage door opener'),
        _state('climate.hallway', 'heat', 'Hallway thermostat', current_temperature=20.5, temperature=21),
        _state('lock.front_door', 'locked', 'Front door'),
        _state('cover.garage', 'closed', 'Garage door'),
        _state('sensor.power', '412', 'Power', unit_of_measurement='W'),
        _state('person.alex', 'home', 'Alex'),
        _state('person.sam', 'not_home', 'Sam'),
        _state('device_tracker.phone', 'home', 'Phone'),
    ]
    return {r['entity_id']: r for r in rows}


AREAS = [
    ['Living Room', ['light.living_room', 'sensor.power']],
    ['Kitchen', ['light.kitchen', 'switch.coffee']],
    ['Hallway', ['climate.hallway', 'lock.front_door']],
]


class FakeHomeAssistant:
    def __init__(self, token: str = TOKEN):
        self.token = token
        self.states = house()
        self.events: List[Tuple[str, Dict[str, Any]]] = []
        self.services: List[Tuple[str, str, Dict[str, Any]]] = []
        self.server = FakeServer(self._answer)
        self.url = self.server.url

    @property
    def requests(self) -> List[Recorded]:
        return self.server.requests

    def close(self) -> None:
        self.server.close()

    def _answer(self, rec: Recorded) -> Tuple[int, Any]:
        if rec.headers.get('Authorization') != f'Bearer {self.token}':
            return 401, {'message': 'Unauthorized'}
        if rec.method == 'GET' and rec.path == '/api/':
            return 200, {'message': 'API running.'}
        if rec.method == 'GET' and rec.path == '/api/states':
            return 200, list(self.states.values())
        if rec.method == 'POST' and rec.path.startswith('/api/events/'):
            event_type = rec.path.rsplit('/', 1)[-1]
            self.events.append((event_type, rec.json() or {}))
            return 200, {'message': f'Event {event_type} fired.'}
        if rec.method == 'POST' and rec.path.startswith('/api/services/'):
            _, _, _, domain, service = rec.path.split('/')
            body = rec.json() or {}
            self.services.append((domain, service, body))
            targets = body.get('entity_id') or []
            targets = [targets] if isinstance(targets, str) else targets
            changed = []
            for entity_id in targets:
                row = self.states.get(entity_id)
                if row is None:
                    continue
                if service in ('turn_off', 'lock', 'close_cover'):
                    row['state'] = {'turn_off': 'off', 'lock': 'locked', 'close_cover': 'closed'}[service]
                elif service in ('turn_on', 'unlock', 'open_cover'):
                    row['state'] = {'turn_on': 'on', 'unlock': 'unlocked', 'open_cover': 'open'}[service]
                changed.append(row)
            return 200, changed
        if rec.method == 'POST' and rec.path == '/api/template':
            template = (rec.json() or {}).get('template') or ''
            if 'areas()' in template:
                return 200, json.dumps(AREAS)
            return 400, {'message': 'Error rendering template'}
        return 404, {'message': 'Not found'}
