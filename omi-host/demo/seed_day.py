"""Seed one family day for omi-host/demo/caldav_demo.py, with the backend's own modules.

Runs on stdin inside the backend's environment (the driver pipes it in):

  local:   cd backend && PYTHONPATH=. .venv/bin/python - < seed_day.py
  docker:  docker exec -i omi-local bash -c 'cd /app/backend && PYTHONPATH=. python -' < seed_day.py

Reads one JSON object from the OMI_DEMO_SEED environment variable and prints one JSON
object back. What it does, by "op":

  account       the login account (Auth emulator user + the local-login credential,
                as backend/scripts/seed_local_account.py makes it) -> {"uid"}
  conversations completed conversations with what the extractor would have produced
                (title, overview, structured events) -> {"ids": [...]}
  allglance     the user's "allglance" task integration pointing at a gateway URL, which
                the family calendar writes through (not made the default task integration)

There is no local LLM in the demo, so conversations are seeded with their extraction
results instead of being processed; everything after this goes through the HTTP API.
"""

import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

if not os.environ.get('FIRESTORE_EMULATOR_HOST'):
    # docker exec does not inherit the backend's environment; borrow it like the
    # backend/scripts helpers do.
    sys.path.insert(0, os.path.join(os.getcwd(), 'scripts'))
    from _container_env import inherit_pid1_env  # type: ignore

    inherit_pid1_env()


def _post(url, payload):
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'}, method='POST'
    )
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        return {'error': error.read().decode('utf-8', 'replace')}


def account(spec):
    from utils.local_auth import local_account_email, set_local_account

    host = os.environ['FIREBASE_AUTH_EMULATOR_HOST']
    base = f'http://{host}/identitytoolkit.googleapis.com/v1/accounts'
    email = local_account_email(spec['username'])
    out = _post(f'{base}:signUp?key=demo', {'email': email, 'password': spec['password'], 'returnSecureToken': True})
    if 'localId' not in out:
        out = _post(
            f'{base}:signInWithPassword?key=demo',
            {'email': email, 'password': spec['password'], 'returnSecureToken': True},
        )
    uid = out['localId']
    set_local_account(spec['username'], uid, spec['password'])
    return {'uid': uid}


def conversations(spec):
    import database.conversations as conversations_db
    from models.conversation import Conversation

    ids = []
    for item in spec['conversations']:
        started = datetime.fromisoformat(item['started_at'])
        finished = started + timedelta(minutes=item.get('minutes', 4))
        cid = item.get('id') or str(uuid.uuid4())
        conversation = Conversation(
            id=cid,
            created_at=started,
            started_at=started,
            finished_at=finished,
            structured={
                'title': item['title'],
                'overview': item['overview'],
                'emoji': item.get('emoji', '👪'),
                'category': 'family',
                'events': item.get('events', []),
            },
            transcript_segments=[
                {'text': line, 'speaker': f'SPEAKER_0{i % 2}', 'speaker_id': i % 2, 'is_user': i % 2 == 0,
                 'start': i * 5.0, 'end': i * 5.0 + 4.5}
                for i, line in enumerate(item.get('transcript', []))
            ],
            status='completed',
            language='en',
        )
        conversations_db.upsert_conversation_with_lifecycle(spec['uid'], conversation.model_dump())
        ids.append(cid)
    return {'ids': ids}


def allglance(spec):
    import database.users as users_db

    users_db.set_task_integration(
        spec['uid'],
        'allglance',
        {'connected': True, 'base_url': spec['url'], 'access_token': spec['token'], 'source': 'vikunja'},
    )
    return {'ok': True}


def main():
    spec = json.loads(os.environ['OMI_DEMO_SEED'])
    result = {'account': account, 'conversations': conversations, 'allglance': allglance}[spec['op']](spec)
    # Last line of stdout is the answer (the backend's imports may log above it).
    print('\n' + json.dumps(result, default=str))


main()
