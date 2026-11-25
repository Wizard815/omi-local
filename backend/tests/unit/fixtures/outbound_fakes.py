"""Test doubles for the outbound integrations (omi-local): a small in-memory Firestore and
recording HTTP servers that answer like the real targets.

The fake servers answer with the shapes the targets' own code produces; each handler cites
where (paths are in the Wizard815 repositories):

- wiki: LLM-will-remember-this/.obsidian/plugins/llmwiki-mcp-bridge/main.js handleFamilyRpc
  (tools/call -> {result:{content:[{type:'text',text}], isError?}}), memoryAddSource (path
  ``raw/sources/<date> - <title>.md``, "already exists" when the path is taken);
- Hermes: hermes-agent/gateway/platforms/api_server_runs.py _accepted_response (202
  {run_id,status,replayed}), _set_run_status ({object:'hermes.run',run_id,status,output?,
  approval?}), _handle_run_approval ({choice,request_id} -> hermes.run.approval_response);
- Odysseus: odysseus/routes/session_routes.py create_session (form fields -> SessionResponse
  {id,name,model}), routes/chat_routes.py chat_endpoint (JSON {message,session} ->
  {response,model,...}); auth app.py AuthMiddleware ("Bearer ody_...");
- Studio: hermes-studio/packages/server/src/modules/studio/controllers/orb.ts startWork
  ({work:{sessionId,status,...}}), services/webhooks/dispatcher.ts signChatWebhookPayload,
  services/omi/scope.ts + key-auth.ts (an ``sk-studio-omi-`` key only in the Authorization
  header, only on Omi's paths: else 403/401), services/omi/service.ts approvals/decide (work in
  a chat Omi did not start is 404) and services/agent-work/service.ts decide ({work, changed,
  message}; the first answer wins, later ones get "Already approved in Studio by Robin."; work no
  longer awaiting approval without a decision is 409);
- Coinpile: coinpile/src/coinpile/agent.py mcp_handle (change tools answer an action_id and
  a note that nothing changed until Yes);
- Orb: pondering-orb/server/src/tools.ts orb_add_log ({project,text}).
"""

from __future__ import annotations

import copy
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

# ---- Firestore --------------------------------------------------------------------------------


def _deep_merge(base: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class _Snapshot:
    def __init__(self, doc_id: str, data: Optional[Dict[str, Any]], reference: Any = None):
        self.id = doc_id
        self._data = copy.deepcopy(data)
        self.exists = data is not None
        self.reference = reference

    def to_dict(self) -> Optional[Dict[str, Any]]:
        return copy.deepcopy(self._data)


class FakeDocument:
    def __init__(self, db: 'FakeFirestore', path: Tuple[str, ...]):
        self._db = db
        self._path = path
        self.id = path[-1]

    def collection(self, name: str) -> 'FakeCollection':
        return FakeCollection(self._db, self._path + (name,))

    def get(self, *args: Any, **kwargs: Any) -> _Snapshot:
        return _Snapshot(self.id, self._db.docs.get(self._path), self)

    def set(self, data: Dict[str, Any], merge: bool = False) -> None:
        with self._db.lock:
            if merge and self._path in self._db.docs:
                self._db.docs[self._path] = _deep_merge(self._db.docs[self._path], data)
            else:
                self._db.docs[self._path] = copy.deepcopy(data)

    def update(self, data: Dict[str, Any]) -> None:
        with self._db.lock:
            if self._path not in self._db.docs:
                raise KeyError('not found')
            self._db.docs[self._path] = {**self._db.docs[self._path], **copy.deepcopy(data)}

    def delete(self) -> None:
        with self._db.lock:
            self._db.docs.pop(self._path, None)


class FakeCollection:
    def __init__(self, db: 'FakeFirestore', path: Tuple[str, ...]):
        self._db = db
        self._path = path
        self._order: Optional[Tuple[str, bool]] = None
        self._limit: Optional[int] = None

    def document(self, name: str) -> FakeDocument:
        return FakeDocument(self._db, self._path + (name,))

    def order_by(self, field: str, direction: Any = None) -> 'FakeCollection':
        query = FakeCollection(self._db, self._path)
        query._limit = self._limit
        query._order = (field, str(direction or '').upper().startswith('DESC'))
        return query

    def limit(self, count: int) -> 'FakeCollection':
        query = FakeCollection(self._db, self._path)
        query._order = self._order
        query._limit = count
        return query

    def stream(self, *args: Any, **kwargs: Any):
        rows = [
            (path, data)
            for path, data in list(self._db.docs.items())
            if len(path) == len(self._path) + 1 and path[:-1] == self._path
        ]
        if self._order:
            field, desc = self._order
            rows.sort(key=lambda r: str(r[1].get(field) or ''), reverse=desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        return [_Snapshot(path[-1], data, FakeDocument(self._db, path)) for path, data in rows]


class FakeFirestore:
    def __init__(self) -> None:
        self.docs: Dict[Tuple[str, ...], Dict[str, Any]] = {}
        self.lock = threading.RLock()

    def collection(self, name: str) -> FakeCollection:
        return FakeCollection(self, (name,))

    def dump(self, prefix: Tuple[str, ...]) -> Dict[str, Dict[str, Any]]:
        return {'/'.join(p): d for p, d in self.docs.items() if p[: len(prefix)] == prefix}


# ---- HTTP -------------------------------------------------------------------------------------


class Recorded:
    def __init__(self, method: str, path: str, query: Dict[str, List[str]], headers: Dict[str, str], body: bytes):
        self.method = method
        self.path = path
        self.query = query
        self.headers = headers
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body.decode() or 'null')

    def form(self) -> Dict[str, str]:
        return {k: v[0] for k, v in parse_qs(self.body.decode()).items()}


Responder = Callable[[Recorded], Tuple[int, Any]]


class FakeServer:
    """A threaded HTTP server on 127.0.0.1 that records every request and answers through
    ``responder(request) -> (status, json_body)``."""

    def __init__(self, responder: Responder):
        self.requests: List[Recorded] = []
        self.responder = responder
        server = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self) -> None:
                length = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(length) if length else b''
                parsed = urlparse(self.path)
                rec = Recorded(self.command, parsed.path, parse_qs(parsed.query), dict(self.headers), body)
                server.requests.append(rec)
                status, payload = server.responder(rec)
                data = b'' if payload is None else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle

            def log_message(self, *args: Any) -> None:  # quiet
                return

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.httpd.server_address[1]}'
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def mcp_text(text: str, *, error: bool = False, rpc_id: Any = 1) -> Dict[str, Any]:
    result: Dict[str, Any] = {'content': [{'type': 'text', 'text': text}]}
    if error:
        result['isError'] = True
    return {'jsonrpc': '2.0', 'id': rpc_id, 'result': result}


class WikiFake:
    """The wiki bridge's /family-mcp: memory_add_source never overwrites (main.js memoryAddSource);
    conversation_log files a conversation (main.js conversationLog)."""

    def __init__(self, token: str = 't-wizard', person_dir: str = 'people/wizard'):
        self.token = token
        self.person_dir = person_dir
        self.files: Dict[str, str] = {}
        self.logged: List[Dict[str, Any]] = []
        self.server = FakeServer(self._respond)

    def _respond(self, rec: Recorded) -> Tuple[int, Any]:
        if rec.headers.get('Authorization') != f'Bearer {self.token}':
            return 401, {
                'jsonrpc': '2.0',
                'id': None,
                'error': {'code': -32001, 'message': 'A family token is required'},
            }
        msg = rec.json()
        if msg.get('method') == 'initialize':
            return 200, {'jsonrpc': '2.0', 'id': msg.get('id'), 'result': {'serverInfo': {'name': 'llmwiki-family'}}}
        params = msg.get('params') or {}
        name, args = params.get('name'), params.get('arguments') or {}
        if name == 'whoami':
            return 200, mcp_text('You are Wizard (member).')
        if name == 'memory_add_source':
            path = f"{self.person_dir}/raw/sources/{args.get('date')} - {args.get('title')}.md"
            if path in self.files:
                return 200, mcp_text(
                    f'{path} already exists and sources are never overwritten; use another title.', error=True
                )
            self.files[path] = str(args.get('content'))
            return 200, mcp_text(f'Added {path} (status: unprocessed) for the Ingest workflow.')
        if name == 'conversation_log':
            # main.js conversationLog: "Conversation - <title> (<id8>)", the overview and action
            # items above the transcript; the same id again answers "Already filed" (not an error).
            self.logged.append(args)
            segments = args.get('segments') or []
            if not segments and not args.get('overview') and not args.get('action_items'):
                return 200, mcp_text('conversation_log: segments is empty: nothing to file.', error=True)
            short = ''.join(c for c in str(args.get('id') or '') if c.isalnum() or c == '-')[:8]
            title = f"Conversation - {args.get('title') or 'untitled'}" + (f' ({short})' if short else '')
            path = f"{self.person_dir}/raw/sources/{args.get('date')} - {title}.md"
            if path in self.files:
                return 200, mcp_text(f'Already filed for {self.person_dir} (same conversation id): left as it was.')
            parts = []
            if args.get('overview'):
                parts.append(f"## Overview\n\n{args['overview']}")
            if args.get('action_items'):
                rows = [f"- [{'x' if i.get('completed') else ' '}] {i['description']}" for i in args['action_items']]
                parts.append('## Action items\n\n' + '\n'.join(rows))
            if segments:
                lines = [f"**{x.get('speaker') or 'Speaker'}:** {x['text']}" for x in segments]
                parts.append('## Transcript\n\n' + '\n\n'.join(lines))
            self.files[path] = '\n\n'.join(parts)
            return 200, mcp_text(
                f'Added {path} (status: unprocessed) for the Ingest workflow.\nSpeakers: Wizard (wizard).'
            )
        return 200, {
            'jsonrpc': '2.0',
            'id': msg.get('id'),
            'error': {'code': -32602, 'message': f'Unknown tool: {name}'},
        }

    @property
    def url(self) -> str:
        return self.server.url

    def close(self) -> None:
        self.server.close()


class HermesFake:
    """Hermes API server /v1/runs: queued -> waiting_for_approval -> completed after the approval."""

    def __init__(self, key: str = 'hermes-key-0123456789', *, needs_approval: bool = True, output: str = 'done'):
        self.key = key
        self.needs_approval = needs_approval
        self.output = output
        self.runs: Dict[str, Dict[str, Any]] = {}
        self.server = FakeServer(self._respond)

    def _respond(self, rec: Recorded) -> Tuple[int, Any]:
        if rec.headers.get('Authorization') != f'Bearer {self.key}':
            return 401, {'error': {'message': 'Invalid API key'}}
        if rec.method == 'GET' and rec.path == '/v1/models':
            return 200, {'object': 'list', 'data': [{'id': 'hermes-agent'}]}
        if rec.method == 'POST' and rec.path == '/v1/runs':
            run_id = f'run_{len(self.runs) + 1:04d}'
            self.runs[run_id] = {'body': rec.json(), 'polls': 0, 'approved': None}
            return 202, {'run_id': run_id, 'status': 'started', 'replayed': False}
        parts = rec.path.strip('/').split('/')
        if len(parts) >= 3 and parts[:2] == ['v1', 'runs'] and parts[2] in self.runs:
            run = self.runs[parts[2]]
            if rec.method == 'POST' and parts[-1] == 'approval':
                body = rec.json()
                run['approved'] = body.get('choice')
                return 200, {
                    'object': 'hermes.run.approval_response',
                    'run_id': parts[2],
                    'choice': body.get('choice'),
                    'resolved': 1,
                }
            run['polls'] += 1
            status = {'object': 'hermes.run', 'run_id': parts[2], 'session_id': run['body'].get('session_id')}
            if self.needs_approval and run['approved'] is None:
                status.update(
                    status='waiting_for_approval',
                    approval={
                        'request_id': 'req-1',
                        'command': 'rm -rf /tmp/scratch',
                        'description': 'Clean scratch',
                        'choices': ['once', 'session', 'always', 'deny'],
                    },
                )
            elif run['approved'] == 'deny':
                status.update(status='failed', error='approval denied')
            else:
                status.update(status='completed', output=self.output)
            return 200, status
        return 404, {'error': {'message': 'not found'}}

    @property
    def url(self) -> str:
        return self.server.url

    def close(self) -> None:
        self.server.close()


class OdysseusFake:
    """Odysseus: POST /api/session (form) -> {id,name,model}; POST /api/chat {message,session} -> {response}."""

    def __init__(self, token: str = 'ody_testtoken0123456789', reply: str = 'Here is the short answer.'):
        self.token = token
        self.reply = reply
        self.sessions: Dict[str, Dict[str, str]] = {}
        self.server = FakeServer(self._respond)

    def _respond(self, rec: Recorded) -> Tuple[int, Any]:
        if rec.headers.get('Authorization') != f'Bearer {self.token}':
            return 401, {'error': 'Invalid API token'}
        if rec.method == 'GET' and rec.path == '/api/models':
            return 200, {'models': [{'id': 'llama'}]}
        if rec.method == 'POST' and rec.path == '/api/session':
            form = rec.form()
            if not form.get('endpoint_id'):
                return 403, {'detail': 'Choose a registered model endpoint'}
            sid = f'sess-{len(self.sessions) + 1}'
            self.sessions[sid] = form
            return 200, {
                'id': sid,
                'name': form.get('name', ''),
                'model': form.get('model', ''),
                'rag': False,
                'archived': False,
            }
        if rec.method == 'POST' and rec.path == '/api/chat':
            body = rec.json()
            if body.get('session') not in self.sessions:
                return 404, {'detail': f"Session '{body.get('session')}' not found"}
            return 200, {'response': self.reply, 'model': 'llama', 'requested_model': 'llama'}
        return 404, {'detail': 'not found'}

    @property
    def url(self) -> str:
        return self.server.url

    def close(self) -> None:
        self.server.close()


class StudioFake:
    """Studio with a Studio key made for Omi: /api/studio/orb/work (start, list), Omi's Agent work
    approvals and their approve/reject, answering like Studio (see the module docstring)."""

    VIA_WORDS = {'studio': 'Studio', 'mattermost': 'Mattermost', 'orb': 'Pondering Orb', 'omi': 'Omi'}
    ALLOWED = (
        ('GET', re.compile(r'^/api/studio/(?:omi|orb)/work/?$')),
        ('POST', re.compile(r'^/api/studio/(?:omi|orb)/work/?$')),
        ('GET', re.compile(r'^/api/studio/omi/approvals/?$')),
        ('POST', re.compile(r'^/api/studio/agent-work/[A-Za-z0-9_-]{1,80}/(?:approve|reject)/?$')),
    )

    def __init__(self, token: str = 'sk-studio-omi-test0123456789', key_name: str = 'omi-home'):
        self.token = token
        self.key_name = key_name
        self.work: List[Dict[str, Any]] = []
        self.agent_work: Dict[str, Dict[str, Any]] = {}
        self.server = FakeServer(self._respond)

    def add_agent_work(self, work_id: str, session_id: str, **fields: Any) -> Dict[str, Any]:
        row = {
            'id': work_id,
            'title': fields.pop('title', 'Merge the shelf branch'),
            'vikunjaTaskId': '',
            'agent': fields.pop('agent', 'claude-code'),
            'sessionId': session_id,
            'status': fields.pop('status', 'awaiting_approval'),
            'summary': fields.pop('summary', 'Tests pass; ready to merge.'),
            'decision': '',
            'decidedVia': '',
            'decidedBy': '',
            'decidedAt': 0,
            'createdAt': 1764000000000,
            'updatedAt': 1764000000000,
            **fields,
        }
        self.agent_work[work_id] = row
        return row

    def decide(self, work_id: str, verdict: str, via: str, by: str) -> None:
        """Someone answers elsewhere (Studio, Mattermost, Orb)."""
        row = self.agent_work[work_id]
        row.update(
            status='done' if verdict == 'approved' else 'rejected', decision=verdict, decidedVia=via, decidedBy=by
        )

    def _omi_session(self, session_id: str) -> bool:
        return any(w.get('sessionId') == session_id for w in self.work)

    def _respond(self, rec: Recorded) -> Tuple[int, Any]:
        header = rec.headers.get('Authorization') or ''
        if any(v and v[0].startswith('sk-studio-omi-') for v in rec.query.values()):
            return 401, {'error': 'Send the Omi key in the Authorization header.'}
        if header != f'Bearer {self.token}':
            return 401, {'error': 'This Omi key is not valid any more. Make a new one in Studio: Connections → Omi.'}
        if not any(m == rec.method and p.match(rec.path) for m, p in self.ALLOWED):
            return 403, {
                'error': "This key is for Omi: it can only start and follow Omi's work chats and answer their approvals."
            }
        if rec.path.endswith('/work') and rec.method == 'GET':
            return 200, {'work': self.work}
        if rec.path.endswith('/work') and rec.method == 'POST':
            body = rec.json()
            item = {
                'sessionId': f'studio-sess-{len(self.work) + 1}',
                'title': body.get('title') or '',
                'agentId': body.get('agentId'),
                'orb': {},
                'status': 'working',
                'startedAt': 1,
                'updatedAt': 1,
            }
            self.work.insert(0, item)
            return 200, {'work': item}
        if rec.path == '/api/studio/omi/approvals':
            waiting = [
                w
                for w in self.agent_work.values()
                if w['status'] == 'awaiting_approval' and self._omi_session(w['sessionId'])
            ]
            return 200, {'approvals': waiting}
        _, _, _, _, work_id, decision = rec.path.rstrip('/').split('/')
        row = self.agent_work.get(work_id)
        if row is None or not self._omi_session(row['sessionId']):
            return 404, {'error': 'No such agent work'}
        by = str((rec.json() or {}).get('by') or '').strip()[:60]
        if row['status'] != 'awaiting_approval':
            if row['decision']:
                who = f" by {row['decidedBy']}" if row['decidedBy'] else ''
                message = f"Already {row['decision']} in {self.VIA_WORDS[row['decidedVia']]}{who}."
                return 200, {'work': row, 'changed': False, 'message': message}
            return 409, {'error': f"This work is {row['status']}, not awaiting approval"}
        verdict = 'approved' if decision == 'approve' else 'rejected'
        self.decide(work_id, verdict, 'omi', f'{by} (Omi)' if by else f'Omi ({self.key_name})')
        return 200, {'work': row, 'changed': True, 'message': f'{verdict.capitalize()}.'}

    @property
    def url(self) -> str:
        return self.server.url

    def close(self) -> None:
        self.server.close()


class McpFake:
    """Coinpile /api/v1/mcp or Orb /mcp: records tools/call; change tools answer like Coinpile's ask."""

    def __init__(self, token: str, path: str):
        self.token = token
        self.path = path
        self.calls: List[Dict[str, Any]] = []
        self.server = FakeServer(self._respond)

    def _respond(self, rec: Recorded) -> Tuple[int, Any]:
        if rec.headers.get('Authorization') != f'Bearer {self.token}':
            return 401, {'detail': 'bad token'}
        if rec.path != self.path:
            return 404, {'detail': 'not found'}
        msg = rec.json()
        if msg.get('method') == 'initialize':
            return 200, {'jsonrpc': '2.0', 'id': msg.get('id'), 'result': {'serverInfo': {'name': 'fake'}}}
        params = msg.get('params') or {}
        self.calls.append({'name': params.get('name'), 'arguments': params.get('arguments') or {}})
        if params.get('name') == 'list_accounts':
            return 200, mcp_text(json.dumps([{'account_id': 'ff:1', 'name': 'Cash', 'type': 'cash'}]))
        if params.get('name') in ('write_note', 'record_transaction'):
            return 200, mcp_text(
                json.dumps(
                    {
                        'action_id': 7,
                        'asked': True,
                        'note': "Nothing has changed yet: the user answers Yes or No on Coinpile's Agent page.",
                    }
                )
            )
        return 200, mcp_text(json.dumps({'id': 'log-1', 'text': (params.get('arguments') or {}).get('text')}))

    @property
    def url(self) -> str:
        return self.server.url

    def close(self) -> None:
        self.server.close()
