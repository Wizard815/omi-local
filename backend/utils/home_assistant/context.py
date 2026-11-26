"""What the agent may read from Home Assistant (omi-local): compact, and only what the
operator allowed (OMI_HA_READ_ALLOW, utils/home_assistant/settings.py).

- ``get_states``: allowed entities, optionally of one domain and/or one area;
- ``whos_home``: who is home and who is away (person.*, else device_tracker.*);
- ``area_summary``: per area, how many allowed entities, which lights are on, the
  temperature where a climate entity reports one.

Areas come from Home Assistant's template API (``areas()`` / ``area_entities()``), the
only REST surface that knows them. Each entity is a few fields, never the whole attribute
map, so a large house still fits in a chat turn.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from utils.home_assistant.runner import db
from utils.home_assistant import client
from utils.home_assistant import settings as ha_settings

MAX_ENTITIES = 60
MAX_LIMIT = 200
KEPT_ATTRIBUTES = (
    'unit_of_measurement',
    'brightness',
    'current_temperature',
    'temperature',
    'hvac_action',
    'hvac_mode',
    'media_title',
    'volume_level',
    'source',
)
AREAS_TEMPLATE = (
    '{% set ns = namespace(rows=[]) %}{% for a in areas() %}'
    '{% set ns.rows = ns.rows + [[area_name(a), area_entities(a)]] %}{% endfor %}{{ ns.rows | tojson }}'
)


async def connection(uid: str) -> Dict[str, Any]:
    settings = await db(ha_settings.effective, uid)
    if not ha_settings.ready(settings):
        raise client.HomeAssistantError('unconfigured', 'Home Assistant is not set up for this user')
    return settings


def compact(row: Dict[str, Any]) -> Dict[str, Any]:
    attributes = row.get('attributes') or {}
    out: Dict[str, Any] = {
        'entity_id': row['entity_id'],
        'name': str(attributes.get('friendly_name') or row['entity_id'])[:80],
        'state': str(row.get('state') or '')[:80],
    }
    for key in KEPT_ATTRIBUTES:
        value = attributes.get(key)
        if isinstance(value, (str, int, float, bool)) and value != '':
            out[key] = value if not isinstance(value, str) else value[:80]
    return out


async def areas(settings: Dict[str, Any]) -> Dict[str, List[str]]:
    """{area name: [entity ids]} from Home Assistant's area registry."""
    rows = await client.template_json(settings, AREAS_TEMPLATE)
    out: Dict[str, List[str]] = {}
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, list) and len(row) == 2 and isinstance(row[1], list):
            out[str(row[0])] = [str(e) for e in row[1]]
    return out


def _find_area(found: Dict[str, List[str]], area: str) -> Optional[str]:
    wanted = area.strip().lower()
    for name in found:
        if name.lower() == wanted:
            return name
    for name in found:
        if wanted in name.lower():
            return name
    return None


async def get_states(
    uid: str, *, domain: str = '', area: str = '', limit: int = MAX_ENTITIES, settings: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    settings = settings or await connection(uid)
    allowed = ha_settings.read_allow()
    rows = [r for r in await client.states(settings) if ha_settings.may_read(r['entity_id'], allowed)]
    domain = (domain or '').strip().lower()
    if domain:
        rows = [r for r in rows if ha_settings.domain_of(r['entity_id']) == domain]
    area_name = ''
    if area and area.strip():
        found = await areas(settings)
        area_name = _find_area(found, area) or ''
        if not area_name:
            return {
                'entities': [],
                'count': 0,
                'area': area,
                'error': f'No area named {area!r}',
                'areas': sorted(found),
            }
        members = set(found[area_name])
        rows = [r for r in rows if r['entity_id'] in members]
    rows.sort(key=lambda r: r['entity_id'])
    limit = max(1, min(int(limit or MAX_ENTITIES), MAX_LIMIT))
    out: Dict[str, Any] = {'entities': [compact(r) for r in rows[:limit]], 'count': len(rows)}
    if len(rows) > limit:
        out['truncated'] = True
    if area_name:
        out['area'] = area_name
    return out


async def whos_home(uid: str, *, settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    allowed = ha_settings.read_allow()
    if not ({'*', 'person', 'device_tracker'} & set(allowed)):
        raise client.HomeAssistantError(
            'refused', 'Presence is not shared: add person to OMI_HA_READ_ALLOW to allow it'
        )
    settings = settings or await connection(uid)
    rows = await client.states(settings)
    people = [r for r in rows if ha_settings.domain_of(r['entity_id']) == 'person']
    if not people:
        people = [r for r in rows if ha_settings.domain_of(r['entity_id']) == 'device_tracker']
    out: List[Dict[str, Any]] = []
    for row in sorted(people, key=lambda r: r['entity_id']):
        name = str((row.get('attributes') or {}).get('friendly_name') or row['entity_id'])[:80]
        state = str(row.get('state') or 'unknown')
        out.append({'name': name, 'state': state, 'since': row.get('last_changed') or ''})
    return {
        'home': [p['name'] for p in out if p['state'] == 'home'],
        'away': [p['name'] for p in out if p['state'] != 'home'],
        'people': out,
    }


async def area_summary(uid: str, *, area: str = '', settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    settings = settings or await connection(uid)
    if area and area.strip():
        return await get_states(uid, area=area, settings=settings)
    allowed = ha_settings.read_allow()
    found = await areas(settings)
    by_id = {r['entity_id']: r for r in await client.states(settings)}
    summary: List[Dict[str, Any]] = []
    for name in sorted(found):
        members = [by_id[e] for e in found[name] if e in by_id and ha_settings.may_read(e, allowed)]
        if not members:
            continue
        row: Dict[str, Any] = {'area': name, 'entities': len(members)}
        lights_on = [compact(m)['name'] for m in members if m['entity_id'].startswith('light.') and m['state'] == 'on']
        if lights_on:
            row['lights_on'] = lights_on
        for member in members:
            current = (member.get('attributes') or {}).get('current_temperature')
            if member['entity_id'].startswith('climate.') and isinstance(current, (int, float)):
                row['temperature'] = current
                break
        summary.append(row)
    return {'areas': summary}
