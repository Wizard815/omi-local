"""A stand-in ntfy / Gotify / webhook receiver for backup_demo.sh and HEALTH.md's dry run.

Accepts any POST and appends one JSON line per message to --log: the path (the ntfy topic,
Gotify's /message), the headers ntfy and Gotify read, and the body. Prints each one too.

  python3 omi-host/demo/fake_ntfy.py --port 8098 --log /tmp/alerts.jsonl
"""

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8098)
    p.add_argument("--log", required=True)
    args = p.parse_args()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8", "replace")
            entry = {
                "at": time.strftime("%H:%M:%S"),
                "path": self.path,
                "title": self.headers.get("Title"),
                "priority": self.headers.get("Priority"),
                "tags": self.headers.get("Tags"),
                "auth": bool(self.headers.get("Authorization") or self.headers.get("X-Gotify-Key")),
                "body": body,
            }
            with open(args.log, "a") as f:
                f.write(json.dumps(entry) + "\n")
            print(f"fake-ntfy {entry['at']} {self.path} {entry['title'] or ''}: {body}", flush=True)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"id":"fake"}')

        def log_message(self, *a):
            pass

    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
