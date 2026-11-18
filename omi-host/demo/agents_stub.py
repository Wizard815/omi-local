#!/usr/bin/env python3
"""Stand-ins for Hermes, Odysseus, Studio, Coinpile and Pondering Orb, for
omi-host/demo/integrations_demo.py. Each records every request it gets (JSON lines in --log)
and answers the way the real service's code does (paths in the Wizard815 repositories):

  hermes    gateway/platforms/api_server_runs.py: POST /v1/runs -> 202 {run_id,status,replayed};
            GET /v1/runs/{id} -> waiting_for_approval (approval.request_id) until
            POST /v1/runs/{id}/approval {choice,request_id}, then completed with output
  odysseus  routes/session_routes.py create_session (form: name, endpoint_id, model ->
            {id,name,model}); routes/chat_routes.py chat_endpoint ({message,session} -> {response})
  studio    controllers/orb.ts startWork ({work:{sessionId,...}}); then, like Studio's webhook
            dispatcher (services/webhooks/dispatcher.ts), POSTs a signed chat.run.completed
            (X-Hermes-Signature-256 = sha256=HMAC(secret, "<ts>.<body>")) to --studio-webhook
  coinpile  src/coinpile/agent.py mcp_handle: write_note / record_transaction only ask
  orb       server/src/tools.ts orb_add_log {project, text}

Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Tuple

LOCK = threading.Lock()


def record(log: str, service: str, method: str, path: str, headers: Dict[str, str], body: bytes) -> None:
    row = {
        'service': service,
        'method': method,
        'path': path,
        'authorization': headers.get('Authorization', ''),
        'idempotency_key': headers.get('Idempotency-Key', ''),
        'content_type': headers.get('Content-Type', ''),
        'body': body.decode('utf-8', 'replace'),
        'at': time.time(),
    }
    with LOCK, open(log, 'a', encoding='utf-8') as out:
        out.write(json.dumps(row) + '\n')


def mcp_text(text: str, rpc_id: Any = 1) -> Dict[str, Any]:
    return {'jsonrpc': '2.0', 'id': rpc_id, 'result': {'content': [{'type': 'text', 'text': text}]}}


class State:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.runs: Dict[str, Dict[str, Any]] = {}
        self.sessions: Dict[str, Dict[str, str]] = {}
        self.work: list = []


def hermes(state: State, method: str, path: str, headers: Dict[str, str], body: bytes) -> Tuple[int, Any]:
    if headers.get('Authorization') != f'Bearer {state.args.hermes_key}':
        return 401, {'error': {'message': 'Invalid API key'}}
    if path == '/v1/models':
        return 200, {'object': 'list', 'data': [{'id': 'hermes-agent'}]}
    if method == 'POST' and path == '/v1/runs':
        run_id = f'run_demo{len(state.runs) + 1}'
        state.runs[run_id] = {'body': json.loads(body or b'{}'), 'approved': None}
        return 202, {'run_id': run_id, 'status': 'started', 'replayed': False}
    parts = path.strip('/').split('/')
    if len(parts) >= 3 and parts[:2] == ['v1', 'runs'] and parts[2] in state.runs:
        run = state.runs[parts[2]]
        if method == 'POST' and parts[-1] == 'approval':
            answer = json.loads(body or b'{}')
            if answer.get('request_id') != 'req-demo-1':
                return 409, {'error': {'message': 'approval_not_pending'}}
            run['approved'] = answer.get('choice')
            return 200, {'object': 'hermes.run.approval_response', 'run_id': parts[2], 'choice': run['approved'], 'resolved': 1}
        status: Dict[str, Any] = {'object': 'hermes.run', 'run_id': parts[2], 'session_id': run['body'].get('session_id')}
        if run['approved'] is None:
            status.update(
                status='waiting_for_approval',
                approval={
                    'request_id': 'req-demo-1',
                    'command': 'python3 cutlist.py --boards 3',
                    'description': 'Run the cut-list script',
                    'choices': ['once', 'session', 'always', 'deny'],
                },
            )
        elif run['approved'] == 'deny':
            status.update(status='failed', error='denied')
        else:
            status.update(status='completed', output='Cut list: three boards of 120 cm, two of 80 cm.')
        return 200, status
    return 404, {'error': {'message': 'not found'}}


def odysseus(state: State, method: str, path: str, headers: Dict[str, str], body: bytes) -> Tuple[int, Any]:
    if headers.get('Authorization') != f'Bearer {state.args.odysseus_token}':
        return 401, {'error': 'Invalid API token'}
    if path == '/api/models':
        return 200, {'models': [{'id': 'llama'}]}
    if method == 'POST' and path == '/api/session':
        form = {k: v[0] for k, v in urllib.parse.parse_qs(body.decode()).items()}
        if not form.get('endpoint_id'):
            return 403, {'detail': 'Choose a registered model endpoint'}
        sid = f'ody-sess-{len(state.sessions) + 1}'
        state.sessions[sid] = form
        return 200, {'id': sid, 'name': form.get('name', ''), 'model': form.get('model', ''), 'rag': False, 'archived': False}
    if method == 'POST' and path == '/api/chat':
        msg = json.loads(body or b'{}')
        if msg.get('session') not in state.sessions:
            return 404, {'detail': 'Session not found'}
        return 200, {'response': 'Boards for garage shelves: 120 cm is the usual length.', 'model': 'llama'}
    return 404, {'detail': 'not found'}


def _studio_webhook(state: State, session_id: str) -> None:
    time.sleep(1.0)
    event = {
        'schema_version': 1,
        'id': f'evt-{session_id}',
        'type': 'chat.run.completed',
        'occurred_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'profile': 'default',
        'source': 'coding_agent',
        'agent': 'claude-code',
        'subject': {'session_id': session_id, 'run_id': f'run-{session_id}'},
        'summary': {'status': 'completed'},
        'message': {'role': 'assistant', 'text': 'Wrote cutlist.py and its test; both pass.', 'truncated': False},
    }
    raw = json.dumps(event).encode()
    ts = str(int(time.time()))
    signature = 'sha256=' + hmac.new(state.args.studio_secret.encode(), f'{ts}.'.encode() + raw, hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        state.args.studio_webhook,
        data=raw,
        method='POST',
        headers={'Content-Type': 'application/json', 'X-Hermes-Event': event['type'], 'X-Hermes-Timestamp': ts, 'X-Hermes-Signature-256': signature},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=30) as response:
            outcome = response.status
    except urllib.error.HTTPError as error:
        outcome = error.code
    record(state.args.log, 'studio-webhook', 'POST', state.args.studio_webhook, {}, json.dumps({'status': outcome}).encode())


def studio(state: State, method: str, path: str, headers: Dict[str, str], body: bytes) -> Tuple[int, Any]:
    if headers.get('Authorization') != f'Bearer {state.args.studio_token}':
        return 401, {'error': 'Sign in to Studio first'}
    if path == '/api/studio/orb/work' and method == 'GET':
        return 200, {'work': state.work}
    if path == '/api/studio/orb/work' and method == 'POST':
        msg = json.loads(body or b'{}')
        item = {'sessionId': f'studio-sess-{len(state.work) + 1}', 'title': msg.get('title') or '', 'agentId': msg.get('agentId'), 'orb': {}, 'status': 'working', 'startedAt': 1, 'updatedAt': 1}
        state.work.insert(0, item)
        threading.Thread(target=_studio_webhook, args=(state, item['sessionId']), daemon=True).start()
        return 200, {'work': item}
    return 404, {'error': 'not found'}


def mcp_service(token: str, mcp_path: str):
    def handle(state: State, method: str, path: str, headers: Dict[str, str], body: bytes) -> Tuple[int, Any]:
        if headers.get('Authorization') != f'Bearer {token}':
            return 401, {'detail': 'bad token'}
        if path != mcp_path:
            return 404, {'detail': 'not found'}
        msg = json.loads(body or b'{}')
        if msg.get('method') == 'initialize':
            return 200, {'jsonrpc': '2.0', 'id': msg.get('id'), 'result': {'serverInfo': {'name': 'stub'}}}
        params = msg.get('params') or {}
        name = params.get('name')
        if name == 'list_accounts':
            return 200, mcp_text(json.dumps([{'account_id': 'ff:1', 'name': 'Cash', 'type': 'cash'}]))
        if name in ('write_note', 'record_transaction'):
            return 200, mcp_text(json.dumps({'action_id': 1, 'note': "Nothing has changed yet: the user answers Yes or No on Coinpile's Agent page."}))
        if name == 'orb_add_log':
            text = (params.get('arguments') or {}).get('text') or ''
            if len(text) > 50:
                return 200, {'jsonrpc': '2.0', 'id': msg.get('id'), 'result': {'content': [{'type': 'text', 'text': 'too long'}], 'isError': True}}
            return 200, mcp_text(json.dumps({'id': 'log-1', 'text': text}))
        return 200, {'jsonrpc': '2.0', 'id': msg.get('id'), 'error': {'code': -32602, 'message': f'unknown tool {name}'}}

    return handle


def serve(state: State, service: str, port: int, handler) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _go(self) -> None:
            length = int(self.headers.get('Content-Length') or 0)
            body = self.rfile.read(length) if length else b''
            path = urllib.parse.urlparse(self.path).path
            headers = dict(self.headers)
            record(state.args.log, service, self.command, path, headers, body)
            status, payload = handler(state, self.command, path, headers, body)
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = _go

        def log_message(self, *args: Any) -> None:
            return

    httpd = ThreadingHTTPServer((state.args.host, port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f'{service} stub on http://{state.args.host}:{port}', flush=True)
    return httpd


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--host', required=True)
    p.add_argument('--hermes-port', type=int, required=True)
    p.add_argument('--odysseus-port', type=int, required=True)
    p.add_argument('--studio-port', type=int, required=True)
    p.add_argument('--coinpile-port', type=int, required=True)
    p.add_argument('--orb-port', type=int, required=True)
    p.add_argument('--log', required=True)
    p.add_argument('--hermes-key', default='hermes-demo-key-0123456789')
    p.add_argument('--odysseus-token', default='ody_demo_token_0123456789')
    p.add_argument('--studio-token', default='studio-demo-jwt')
    p.add_argument('--studio-secret', default='studio-demo-webhook-secret')
    p.add_argument('--studio-webhook', required=True)
    p.add_argument('--coinpile-token', default='cp_wizard_demo')
    p.add_argument('--orb-token', default='orb-demo-token')
    args = p.parse_args()
    state = State(args)
    serve(state, 'hermes', args.hermes_port, hermes)
    serve(state, 'odysseus', args.odysseus_port, odysseus)
    serve(state, 'studio', args.studio_port, studio)
    serve(state, 'coinpile', args.coinpile_port, mcp_service(args.coinpile_token, '/api/v1/mcp'))
    serve(state, 'orb', args.orb_port, mcp_service(args.orb_token, '/mcp'))
    while True:
        time.sleep(3600)


if __name__ == '__main__':
    main()
