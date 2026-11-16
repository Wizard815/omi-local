#!/usr/bin/env python3
"""A stand-in for the ALLGlance gateway's two REST calls the family calendar makes
(backend/utils/family_calendar.py): the household roster and "create a dayGLANCE
event". It accepts every bearer token, numbers the events it is given and appends
each request body to --log (JSON lines) so the demo can show what Omi sent.

Only for omi-host/demo/caldav_demo.sh: the family calendar's approve step refuses
to work without a gateway, and this lets the demo drive that approve/edit path.
"""

import argparse
import itertools
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_ids = itertools.count(1)
_lock = threading.Lock()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--host', default='127.0.0.1', help='0.0.0.0 when the backend runs in a container')
    parser.add_argument('--log', required=True)
    args = parser.parse_args()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            if self.path.startswith('/api/allglance/users'):
                self._send(200, {'users': [{'id': 'parent', 'name': 'Parent'}, {'id': 'kid', 'name': 'Kid'}]})
            else:
                self._send(404, {'error': 'not in the stub'})

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get('Content-Length') or 0)
            body = json.loads(self.rfile.read(length) or b'{}')
            if self.path.startswith('/api/allglance/tasks'):
                with _lock:
                    task_id = next(_ids)
                    with open(args.log, 'a', encoding='utf-8') as log:
                        log.write(json.dumps({'id': task_id, 'body': body}) + '\n')
                self._send(201, {'source': body.get('source') or 'dayglance', 'task': {'id': task_id, **body}})
            else:
                self._send(404, {'error': 'not in the stub'})

        def log_message(self, *args):  # quiet
            pass

    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
