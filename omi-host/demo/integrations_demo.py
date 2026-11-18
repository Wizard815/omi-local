#!/usr/bin/env python3
"""Connections demo driver: what Omi sends to the family wiki, Odysseus, Hermes, Studio, Coinpile
and Pondering Orb, checked at the receiving end, and that nothing leaves without the user's OK.

Plays the Omi app through the backend's HTTP API (local login), against the stack
omi-host/demo/integrations_demo.sh starts: the wiki is the REAL LLMWIKI bridge handler over
the wiki repo's test vault (read back through its /__vault window), the agents are recording
stand-ins (agents_stub.py, their requests in --agents-log). Standard library only; reuses
caldav_demo.py's HTTP client, result table and seeding. Exits 1 when any check FAILs.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from caldav_demo import HttpError, Omi, Results, _request, family_day, seed  # noqa: E402

WIKI_TOKEN = 't-wizard'  # the harness vault's member token for "wizard"
HERMES_KEY = 'hermes-demo-key-0123456789'
ODYSSEUS_TOKEN = 'ody_demo_token_0123456789'
STUDIO_TOKEN = 'studio-demo-jwt'
STUDIO_SECRET = 'studio-demo-webhook-secret'
COINPILE_TOKEN = 'cp_wizard_demo'
ORB_TOKEN = 'orb-demo-token'


class Demo:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.r = Results()
        self.omi = Omi(args.backend)
        self.lan = args.lan
        self.today = datetime.now(timezone.utc).date()
        self.day1 = self.today - timedelta(days=1)
        self.day2 = self.today - timedelta(days=2)
        self.conversations: Dict[str, List[str]] = {}
        self.uid = ''

    def url(self, port: int) -> str:
        return f'http://{self.lan}:{port}'

    # ---- reading the receiving ends --------------------------------------------------------
    def vault(self) -> Dict[str, str]:
        status, _, text = _request('GET', self.url(self.args.wiki_port) + '/__vault')
        return json.loads(text) if status == 200 else {}

    def sources(self, needle: str) -> Dict[str, str]:
        return {p: t for p, t in self.vault().items() if '/raw/sources/' in p and needle in p}

    def agents(self, service: Optional[str] = None) -> List[Dict[str, Any]]:
        try:
            with open(self.args.agents_log, encoding='utf-8') as log:
                rows = [json.loads(line) for line in log if line.strip()]
        except FileNotFoundError:
            return []
        return [r for r in rows if service is None or r['service'] == service]

    def wait(self, what: Callable[[], Any], timeout: float = 45.0) -> Any:
        end = time.time() + timeout
        while time.time() < end:
            value = what()
            if value:
                return value
            time.sleep(1.0)
        return what()

    def check(self, area: str, direction: str, name: str, fn: Callable[[], Any]) -> bool:
        try:
            ok, detail = fn()
        except Exception as error:  # noqa: BLE001 — one broken step is one FAIL row, the rest still run
            ok, detail = False, f'{type(error).__name__}: {error}'
        return self.r.add(area, direction, name, bool(ok), detail)

    # ---- setup -----------------------------------------------------------------------------
    def setup(self) -> None:
        username, password = f'wiki-demo-{secrets.token_hex(3)}', secrets.token_urlsafe(12)
        self.uid = seed(self.args.seed_cmd, {'op': 'account', 'username': username, 'password': password})['uid']
        self.omi.login(username, password)
        for day in (self.day1, self.day2):
            plan = family_day(day, self.today)
            ids = seed(self.args.seed_cmd, {'op': 'conversations', 'uid': self.uid, 'conversations': plan})['ids']
            self.conversations[day.isoformat()] = ids
        self.r.add('setup', '-', f'local login as {username}; two family days seeded ({self.day2}, {self.day1})', True)

        before = self.omi.call('GET', '/v1/integrations/outbound')
        self.check('settings', '-', 'every connection starts off', lambda: (
            all(not before[t]['enabled'] for t in ('wiki', 'hermes', 'odysseus', 'studio', 'coinpile', 'orb')), ''))
        body = {
            'wiki': {'enabled': True, 'url': self.url(self.args.wiki_port), 'token': WIKI_TOKEN, 'send_day_reviews': True},
            'hermes': {'enabled': True, 'url': self.url(self.args.hermes_port), 'token': HERMES_KEY},
            'odysseus': {'enabled': True, 'url': self.url(self.args.odysseus_port), 'token': ODYSSEUS_TOKEN,
                         'endpoint_id': 'ep-local', 'model': 'llama'},
            'studio': {'enabled': True, 'url': self.url(self.args.studio_port), 'token': STUDIO_TOKEN,
                       'default_agent': 'claude-code'},
            'coinpile': {'enabled': True, 'url': self.url(self.args.coinpile_port), 'token': COINPILE_TOKEN},
            'orb': {'enabled': True, 'url': self.url(self.args.orb_port), 'token': ORB_TOKEN, 'log_day_approvals': True},
        }
        saved = self.omi.call('PUT', '/v1/integrations/outbound', body)
        text = json.dumps(saved)
        self.check('settings', '-', 'PUT /v1/integrations/outbound: tokens write-only (has_token, never echoed)', lambda: (
            all(saved[t]['has_token'] for t in body)
            and not any(tok in text for tok in (WIKI_TOKEN, HERMES_KEY, ODYSSEUS_TOKEN, STUDIO_TOKEN, COINPILE_TOKEN, ORB_TOKEN)),
            ''))
        for target in body:
            result = self.omi.call('POST', f'/v1/integrations/outbound/{target}/test')
            self.check('settings', 'probe', f'Test connection: {target}', lambda r=result: (r['ok'], r.get('error') or r.get('detail')))

    # ---- the wiki: approved days only ----------------------------------------------------------
    def wiki_days(self) -> None:
        d1 = self.day1.isoformat()
        draft = self.omi.call('POST', f'/v1/users/day-reviews/{d1}/bundle')
        self.check('wiki', 'gate', 'the night job writes a summary DRAFT', lambda: (draft.get('draft') is True, ''))
        time.sleep(self.args.settle)
        self.check('wiki', 'gate', 'an unapproved draft is never sent (wiki and Orb get nothing)', lambda: (
            not self.sources('Omi day review') and not [r for r in self.agents('orb') if 'orb_add_log' in r['body']],
            f'{list(self.sources("Omi day review"))}'))

        self.omi.call('POST', f'/v1/users/day-reviews/{d1}/approve')
        view = self.omi.call('GET', f'/v1/users/day-reviews/{d1}?include_drafts=true')['review']
        scheduled = [r for r in view.get('outbound_receipts') or [] if r['target'] == 'wiki']
        self.check('wiki', 'gate', 'approving schedules the send after the Undo window (receipt on the day)', lambda: (
            bool(scheduled) and scheduled[0].get('scheduled_at') and not scheduled[0].get('sent_at'), json.dumps(scheduled)[:200]))
        path = f'people/wizard/raw/sources/{d1} - Omi day review.md'
        files = self.wait(lambda: self.sources(f'{d1} - Omi day review'))
        content = files.get(path, '')
        self.check('wiki', 'send', f'approved day filed by memory_add_source as {path}', lambda: (bool(content), str(list(files))))
        self.check('wiki', 'send', 'the source carries the title, kept conversations and omi:// links', lambda: (
            'Kept conversations' in content and 'omi://conversation/' in content and "Mia's dentist appointment" in content
            and 'status: unprocessed' in content, content[:160].replace('\n', ' | ')))
        view = self.wait(lambda: [r for r in self.omi.call('GET', f'/v1/users/day-reviews/{d1}')['review'].get('outbound_receipts') or []
                                  if r['target'] == 'wiki' and r.get('sent_at')])
        self.check('wiki', 'send', 'the day review shows "sent to the wiki" with the file', lambda: (
            bool(view) and 'Omi day review' in view[0]['detail'], json.dumps(view)[:200]))
        orb = self.wait(lambda: [json.loads(r['body']) for r in self.agents('orb') if 'orb_add_log' in r['body']])
        self.check('orb', 'send', 'Orb gets one short orb_add_log line (<= 50 chars) for the approved day', lambda: (
            len(orb) == 1 and len(orb[0]['params']['arguments']['text']) <= 50 and orb[0]['params']['arguments']['project'] == 'General',
            json.dumps(orb[0]['params']['arguments']) if orb else 'none'))
        time.sleep(self.args.settle)
        self.check('wiki', 'send', 'sent once per approval (no second copy on later ticks)', lambda: (
            len(self.sources(f'{d1} - Omi day review')) == 1, str(list(self.sources(d1)))))

        # Reopen (an all-kept day goes back to unsorted), summarize again and approve again.
        self.omi.call('POST', f'/v1/users/day-reviews/{d1}/reopen')
        if not (self.omi.call('GET', f'/v1/users/day-reviews/{d1}?include_drafts=true') or {}).get('review'):
            self.omi.call('POST', f'/v1/users/day-reviews/{d1}/bundle')
        self.omi.call('POST', f'/v1/users/day-reviews/{d1}/approve')
        revised = self.wait(lambda: self.sources(f'{d1} - Omi day review (revised 1)'))
        self.check('wiki', 'send', 'reopened and approved again: filed as "Omi day review (revised 1)"', lambda: (
            bool(revised), str(list(self.sources(d1)))))

        d2 = self.day2.isoformat()
        self.omi.call('POST', f'/v1/users/day-reviews/{d2}/bundle')
        batch = self.omi.call('POST', '/v1/approvals/batch', {'day_dates': [d2]})
        undo = self.omi.call('POST', '/v1/approvals/batch/undo', {'task_ids': [], 'events': [], 'day_dates': batch['day_dates']})
        time.sleep(self.args.settle)
        self.check('wiki', 'gate', 'Approve all then Undo inside the window: the day is never sent', lambda: (
            batch['day_dates'] == [d2] and undo['day_count'] == 1 and not self.sources(f'{d2} - Omi day review'),
            str(list(self.sources(d2)))))

    # ---- the wiki: one chosen conversation -------------------------------------------------------
    def wiki_conversation(self) -> None:
        cid = self.conversations[self.day1.isoformat()][1]
        preview = self.omi.call('GET', f'/v1/conversations/{cid}/send/preview?target=wiki')
        sent = self.omi.call('POST', f'/v1/conversations/{cid}/send', {'target': 'wiki'})
        files = self.sources(preview['title'])
        body = next(iter(files.values()), '')
        self.check('wiki', 'send', 'Send to wiki files exactly the preview (edited title, overview)', lambda: (
            sent['ok'] and body.rstrip().endswith(preview['content'].rstrip()) and '## Overview' in body, preview['title']))
        again = self.omi.call('POST', f'/v1/conversations/{cid}/send', {'target': 'wiki'})
        self.check('wiki', 'send', 'a second Send says "already sent" (no silent duplicate)', lambda: (
            again.get('already_sent') is True and len(self.sources('Conversation - ')) == 1, ''))
        revised = self.omi.call('POST', f'/v1/conversations/{cid}/send', {'target': 'wiki', 'revised': True})
        self.check('wiki', 'send', 'Send revised copy files "(revised 1)"', lambda: (
            revised['ok'] and revised['receipt']['title'].endswith('(revised 1)'), revised['receipt']['title']))

        hook = f'{self.url(self.args.wiki_port)}/family/conversation?key={WIKI_TOKEN}'
        self.omi.call('POST', '/v1/users/developer/webhook/memory_created', {'url': hook})
        state = self.omi.call('GET', '/v1/integrations/outbound')
        self.check('wiki', 'gate', 'a developer webhook to the same wiki is flagged (and skipped while Send to wiki is on)', lambda: (
            state['conversation_webhook_to_wiki'] is True, ''))

    # ---- agent jobs ---------------------------------------------------------------------------
    def job(self, job_id: str) -> Dict[str, Any]:
        return self.omi.call('GET', f'/v1/agent-jobs/{job_id}')

    def agent_jobs(self) -> None:
        cid = self.conversations[self.day1.isoformat()][0]
        request = {'target': 'hermes', 'prompt': 'Plan the dentist morning and make a checklist.', 'conversation_ids': [cid]}
        preview = self.omi.call('POST', '/v1/agent-jobs/preview', request)
        created = self.omi.call('POST', '/v1/agent-jobs', request)['job']
        run = [json.loads(r['body']) | {'_key': r['idempotency_key']} for r in self.agents('hermes') if r['path'] == '/v1/runs']
        self.check('hermes', 'send', 'POST /v1/runs with exactly the previewed text, session omi-job-<id>, Idempotency-Key', lambda: (
            run and run[-1]['input'] == preview['content'] and run[-1]['session_id'] == f"omi-job-{created['id']}"
            and run[-1]['_key'] == f"omi-job-{created['id']}", created.get('external_id')))
        asking = self.wait(lambda: [j for j in self.omi.call('GET', '/v1/approvals').get('agent_jobs') or [] if j['id'] == created['id']])
        self.check('hermes', 'approve', "Hermes' waiting_for_approval shows in Omi's Approvals with its command", lambda: (
            bool(asking) and asking[0]['approval']['command'] == 'python3 cutlist.py --boards 3', json.dumps(asking[0]['approval']) if asking else 'none'))
        self.omi.call('POST', f"/v1/agent-jobs/{created['id']}/approval", {'choice': 'once'})
        relayed = [json.loads(r['body']) for r in self.agents('hermes') if r['path'].endswith('/approval')]
        self.check('hermes', 'approve', 'Allow once relayed to /v1/runs/{id}/approval with the request_id', lambda: (
            relayed == [{'choice': 'once', 'request_id': 'req-demo-1'}], json.dumps(relayed)))
        done = self.wait(lambda: (lambda j: j if j['status'] == 'completed' else None)(self.job(created['id'])))
        self.check('hermes', 'result', 'the run result is stored on the job', lambda: (
            bool(done) and done['result'].startswith('Cut list'), (done or {}).get('result', '')))

        ody = self.omi.call('POST', '/v1/agent-jobs', {'target': 'odysseus', 'prompt': 'How long should garage shelf boards be?'})['job']
        ody_done = self.wait(lambda: (lambda j: j if j['status'] == 'completed' else None)(self.job(ody['id'])), 20)
        calls = [(r['path'], r['body']) for r in self.agents('odysseus') if r['method'] == 'POST']
        self.check('odysseus', 'send', 'a simple ask: POST /api/session (endpoint_id, model) then /api/chat', lambda: (
            [p for p, _ in calls] == ['/api/session', '/api/chat'] and 'endpoint_id=ep-local' in calls[0][1]
            and json.loads(calls[1][1])['message'] == ody['prompt'], str([p for p, _ in calls])))
        self.check('odysseus', 'result', 'the answer is stored on the job within seconds', lambda: (
            bool(ody_done) and ody_done['result'].startswith('Boards'), (ody_done or {}).get('result', '')))

        studio = self.omi.call('POST', '/v1/agent-jobs', {'target': 'studio', 'prompt': 'Write a cut-list script with a test.'})['job']
        work = [json.loads(r['body']) for r in self.agents('studio') if r['method'] == 'POST']
        self.check('studio', 'send', 'POST /api/studio/orb/work with the agent and the prompt', lambda: (
            work and work[-1]['agentId'] == 'claude-code' and work[-1]['prompt'] == studio['prompt'], json.dumps(work[-1])[:160] if work else 'none'))
        studio_done = self.wait(lambda: (lambda j: j if j['status'] == 'completed' else None)(self.job(studio['id'])), 20)
        hook = self.wait(lambda: [json.loads(r['body']) for r in self.agents('studio-webhook')], 10)
        self.check('studio', 'result', "Studio's signed chat.run.completed webhook is verified and stores the result", lambda: (
            bool(studio_done) and studio_done['result'].startswith('Wrote cutlist.py') and hook and hook[-1]['status'] == 200,
            (studio_done or {}).get('result', '')))
        status, _, _ = _request('POST', f'{self.args.backend}/v1/integrations/studio/events', body=b'{"type":"chat.run.completed","subject":{"session_id":"x"}}',
                                headers={'Content-Type': 'application/json', 'X-Hermes-Signature-256': 'sha256=00', 'X-Hermes-Timestamp': str(int(time.time()))})
        self.check('studio', 'gate', 'an unsigned or unknown Studio event is refused (401)', lambda: (status == 401, str(status)))

    # ---- Coinpile ------------------------------------------------------------------------------
    def coinpile(self) -> None:
        cid = self.conversations[self.day1.isoformat()][2]
        draft = self.omi.call('GET', f'/v1/integrations/coinpile/draft?conversation_id={cid}')
        note = self.omi.call('POST', '/v1/integrations/coinpile/send', {**draft, 'body': 'Cake deposit, $40 cash.'})
        accounts = self.omi.call('GET', '/v1/integrations/coinpile/accounts')['accounts']
        tx = self.omi.call('POST', '/v1/integrations/coinpile/send', {
            'kind': 'transaction', 'conversation_id': cid, 'account_id': accounts[0]['id'], 'amount': -40,
            'description': 'Cake deposit', 'date': self.day1.isoformat()})
        calls = [json.loads(r['body'])['params'] for r in self.agents('coinpile') if '"tools/call"' in r['body']]
        sent = [c for c in calls if c['name'] in ('write_note', 'record_transaction')]
        self.check('coinpile', 'send', 'write_note then record_transaction, exactly the fields shown; Coinpile only asks', lambda: (
            note['ok'] and tx['ok'] and [c['name'] for c in sent] == ['write_note', 'record_transaction']
            and sent[1]['arguments'] == {'account_id': 'ff:1', 'amount': -40.0, 'description': 'Cake deposit', 'date': self.day1.isoformat()}
            and 'Nothing has changed yet' in tx['answer'], json.dumps(sent)[:200]))

    # ---- egress -----------------------------------------------------------------------------------
    def egress(self) -> None:
        probe = subprocess.run(
            [sys.executable, '-c', 'import sitecustomize, socket\n'
             'print(sitecustomize._MODE)\n'
             's = socket.socket()\n'
             'try:\n    s.connect(("1.1.1.1", 80)); print("public-reachable")\n'
             'except Exception as e:\n    print("public-refused")\n'],
            capture_output=True, text=True, env=os.environ, timeout=30)
        self.check('egress', '-', "the backend's interpreter runs omi-host's egress guard in block mode (a public address is refused)",
                   lambda: (probe.stdout.split() == ['block', 'public-refused'], probe.stdout.strip().replace('\n', ' ')))
        refused = []
        try:
            with open(self.args.egress_log, encoding='utf-8') as log:
                refused = [json.loads(l) for l in log if l.strip()]
        except FileNotFoundError:
            pass
        lan = [r for r in refused if self.lan in json.dumps(r)]
        self.check('egress', '-', 'the egress guard (block) let every LAN call through and refused none of them', lambda: (
            not lan, f'{len(refused)} refused/logged in total'))

    def run(self) -> int:
        self.setup()
        for step in (self.wiki_days, self.wiki_conversation, self.agent_jobs, self.coinpile, self.egress):
            try:
                step()
            except (HttpError, Exception) as error:  # noqa: BLE001 — report and go on
                self.r.add(step.__name__, '-', 'step crashed', False, f'{type(error).__name__}: {error}'[:300])
        print()
        print(self.r.table())
        return 1 if any(r.status == 'FAIL' for r in self.r.rows) else 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend', required=True)
    p.add_argument('--seed-cmd', required=True)
    p.add_argument('--lan', required=True)
    for name in ('wiki', 'hermes', 'odysseus', 'studio', 'coinpile', 'orb'):
        p.add_argument(f'--{name}-port', type=int, required=True)
    p.add_argument('--agents-log', required=True)
    p.add_argument('--egress-log', default='')
    p.add_argument('--settle', type=float, default=12.0, help='seconds to wait for "nothing was sent" checks')
    return Demo(p.parse_args()).run()


if __name__ == '__main__':
    sys.exit(main())
