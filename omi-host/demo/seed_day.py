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
  tasks         action items as conversation extraction writes them: waiting for the
                user's OK (utils/task_approval.mark_extracted), created at the given time
                -> {"ids": [...]}
  legacy_sync   one sync of a user with the CalDAV sync code from BEFORE per-person lists and
                approvals (its source is passed in, taken from origin/main by the driver), to
                build the state an existing install has -> the old code's report
  auto_approve  what the hourly scheduler runs for one user: their auto-approve, if it is
                on and due (utils/auto_approve.run_for_user) -> the outcome
  conversation_finished  run the post-processing hook (developer webhook, Home Assistant events)
                for one seeded conversation, as the pipeline does once it is processed
  day_draft     a day summary draft as the overnight job writes it (utils/day_review_service
                .prepare_draft), with the text the model would have written, so approving it
                needs no model call -> {"date"}

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
                'action_items': item.get('action_items', []),
            },
            transcript_segments=[
                {'text': line, 'speaker': f'SPEAKER_0{i % 2}', 'speaker_id': i % 2, 'is_user': i % 2 == 0,
                 'start': i * 5.0, 'end': i * 5.0 + 4.5}
                for i, line in enumerate(item.get('transcript', []))
            ],
            status='completed',
            language='en',
            starred=bool(item.get('starred')),
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


def tasks(spec):
    import database.action_items as action_items_db
    from utils import task_approval

    ids = []
    for item in spec['tasks']:
        data = {
            'description': item['description'],
            'due_at': datetime.fromisoformat(item['due_at']) if item.get('due_at') else None,
            'conversation_id': item.get('conversation_id'),
            'created_at': datetime.fromisoformat(item['created_at']) if item.get('created_at') else None,
            'completed': False,
        }
        rows = [{k: v for k, v in data.items() if v is not None}]
        task_approval.mark_extracted(rows)  # what _write_action_items does before writing
        ids.append(action_items_db.create_action_item(spec['uid'], rows[0]))
    return {'ids': ids}


def legacy_sync(spec):
    import types

    module = types.ModuleType('omi_demo_legacy_caldav_sync')
    sys.modules[module.__name__] = module
    exec(compile(spec['source'], 'legacy_caldav_sync.py', 'exec'), module.__dict__)
    return module.run_for_user(spec['uid']) or {}


def auto_approve(spec):
    from utils import auto_approve as job

    return job.run_for_user(spec['uid']) or {}


def day_draft(spec):
    import database.day_reviews as day_reviews_db

    now = datetime.now(timezone.utc)
    sections = [
        {'id': f's{i + 1}', 'title': s['title'], 'text': s['text'], 'tags': s.get('tags', []),
         'conversation_ids': s['conversation_ids'], 'favorited': False}
        for i, s in enumerate(spec['sections'])
    ]
    day_reviews_db.set_day_review(spec['uid'], spec['date'], {
        'status': 'draft',
        'title': spec['title'],
        'summary_text': '\n\n'.join(f"## {s['title']}\n{s['text']}" for s in sections),
        'sections': sections,
        'corrections': [],
        'kept_conversation_ids': spec.get('kept', []),
        'rolled_up_conversation_ids': spec['rolled_up'],
        'merged_groups': [],
        'dropped_conversation_ids': [],
        'time_zone': spec.get('time_zone', 'UTC'),
        'created_at': now,
        'updated_at': now,
        'generated_at': now,
        'generation_count': 1,
    })
    return {'date': spec['date']}


def conversation_finished(spec):
    """What the pipeline runs once a conversation is processed (utils/webhooks.conversation_created_webhook):
    the developer webhook, and Home Assistant's omi_conversation_finished / omi_task_created."""
    import asyncio

    import database.conversations as conversations_db
    from models.conversation import Conversation
    from utils.webhooks import conversation_created_webhook

    row = conversations_db.get_conversation(spec['uid'], spec['conversation_id'])
    asyncio.run(conversation_created_webhook(spec['uid'], Conversation(**row)))
    return {'conversation_id': spec['conversation_id']}


def main():
    spec = json.loads(os.environ['OMI_DEMO_SEED'])
    ops = {
        'account': account,
        'conversations': conversations,
        'allglance': allglance,
        'tasks': tasks,
        'auto_approve': auto_approve,
        'legacy_sync': legacy_sync,
        'day_draft': day_draft,
        'conversation_finished': conversation_finished,
    }
    result = ops[spec['op']](spec)
    # Last line of stdout is the answer (the backend's imports may log above it).
    print('\n' + json.dumps(result, default=str))


main()
