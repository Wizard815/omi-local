"""A slow OpenAI-compatible model for proxy_demo.sh. Standard library only.

Stands in for llama.cpp on slow hardware: a chat completion whose messages mention
"slowprobe" stays completely silent for --delay seconds (no bytes at all, like a local
model reading a long prompt), then streams a short answer. Every other request answers
at once. Each request is logged as one JSON line to --log.

  python3 slow_llm_stub.py --port 8482 --delay 75 --log /tmp/llm.jsonl

Serves /v1/models, /v1/chat/completions (streamed or not) and /v1/embeddings.
"""

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ANSWER = 'The plumber comes Friday at nine.'
MODEL = 'stub-model'
EMBEDDING_DIMENSIONS = 1536


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    delay = 0.0
    log_path = ''
    lock = threading.Lock()

    def log_message(self, *_args):  # quiet; the JSON log is the record
        return

    def _log(self, **entry):
        if not self.log_path:
            return
        with self.lock, open(self.log_path, 'a', encoding='utf-8') as out:
            out.write(json.dumps({'at': round(time.time(), 3), **entry}) + '\n')

    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip('/').endswith('/models'):
            return self._json(200, {'object': 'list', 'data': [{'id': MODEL, 'object': 'model'}]})
        return self._json(404, {'error': 'not found'})

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        try:
            body = json.loads(self.rfile.read(length) or b'{}')
        except ValueError:
            return self._json(400, {'error': 'bad json'})
        if self.path.endswith('/embeddings'):
            inputs = body.get('input')
            count = len(inputs) if isinstance(inputs, list) else 1
            self._log(path=self.path, count=count)
            vector = [0.001] * EMBEDDING_DIMENSIONS
            return self._json(
                200,
                {
                    'object': 'list',
                    'model': body.get('model') or MODEL,
                    'data': [{'object': 'embedding', 'index': i, 'embedding': vector} for i in range(count)],
                    'usage': {'prompt_tokens': 1, 'total_tokens': 1},
                },
            )
        if not self.path.endswith('/chat/completions'):
            return self._json(404, {'error': 'not found'})

        slow = 'slowprobe' in json.dumps(body.get('messages') or []).lower()
        stream = bool(body.get('stream'))
        self._log(path=self.path, stream=stream, slow=slow, tools=len(body.get('tools') or []))
        if slow:
            time.sleep(self.delay)
        created = int(time.time())
        if not stream:
            return self._json(
                200,
                {
                    'id': 'chatcmpl-stub',
                    'object': 'chat.completion',
                    'created': created,
                    'model': MODEL,
                    'choices': [
                        {'index': 0, 'message': {'role': 'assistant', 'content': ANSWER}, 'finish_reason': 'stop'}
                    ],
                    'usage': {'prompt_tokens': 10, 'completion_tokens': 8, 'total_tokens': 18},
                },
            )
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'close')
        self.end_headers()

        def event(delta, finish=None, usage=None):
            chunk = {
                'id': 'chatcmpl-stub',
                'object': 'chat.completion.chunk',
                'created': created,
                'model': MODEL,
                'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}],
            }
            if usage:
                chunk['usage'] = usage
            self.wfile.write(f'data: {json.dumps(chunk)}\n\n'.encode())
            self.wfile.flush()

        event({'role': 'assistant', 'content': ''})
        for word in ANSWER.split(' '):
            event({'content': word + ' '})
        event({}, finish='stop', usage={'prompt_tokens': 10, 'completion_tokens': 8, 'total_tokens': 18})
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()
        self.close_connection = True


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8482)
    parser.add_argument('--delay', type=float, default=75.0)
    parser.add_argument('--log', default='')
    args = parser.parse_args()
    Handler.delay = args.delay
    Handler.log_path = args.log
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
