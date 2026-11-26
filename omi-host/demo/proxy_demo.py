"""Driver for proxy_demo.sh: the Omi API, live audio and CalDAV through a real NGINX.

Plays the phone app through omi-host/proxy/nginx/omi.conf and checks what a remote
user depends on. A second copy of the same config, with its idle limit cut to
--idle-seconds, stands in for Cloudflare's ~100 s cut of an HTTP response that stays
silent: a model that thinks longer than that must not lose the answer.

Prints a PASS/FAIL table; exits 1 on any FAIL. Arguments come from caldav_demo.sh
(--backend, --caldav, --seed-cmd, ...) plus the proxy ones proxy_demo.sh adds.
"""

import argparse
import base64
import http.client
import json
import os
import re
import subprocess
import sys
import time
import uuid
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
HEARTBEAT = 'think: Still working…'
ANSWER = 'The plumber comes Friday at nine.'
ROWS = []


def row(area, check, ok, detail=''):
    ROWS.append((area, check, 'PASS' if ok else 'FAIL', detail))


def connection(base, timeout=60):
    parts = urlsplit(base)
    return http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout)


def request(base, method, path, body=None, headers=None, timeout=60):
    conn = connection(base, timeout)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()
    finally:
        conn.close()


def seed_account(seed_cmd, username, password):
    with open(os.path.join(HERE, 'seed_day.py'), 'rb') as script:
        proc = subprocess.run(
            ['bash', '-c', seed_cmd],
            stdin=script,
            capture_output=True,
            env={**os.environ, 'OMI_DEMO_SEED': json.dumps({'op': 'account', 'username': username, 'password': password})},
            timeout=180,
        )
    lines = [line for line in proc.stdout.decode('utf-8', 'replace').splitlines() if line.strip()]
    if proc.returncode != 0 or not lines:
        raise RuntimeError(proc.stderr.decode('utf-8', 'replace')[-2000:])
    return json.loads(lines[-1])['uid']


def client_reply(lines):
    """What the phone and desktop clients show: data: chunks; think:, comments, done: are not text.

    The same rules as app/lib/backend/http/api/messages.dart (parseMessageChunk,
    _isIgnorableStreamFrame) and desktop/windows messagesSse.ts (parseMessagesSse).
    """
    out = []
    for line in lines:
        if not line or line.lstrip().startswith(':'):
            continue
        if line.startswith(('done:', 'message:', 'think:', 'tool:', 'memory:')):
            continue
        content = line[5:].lstrip(' ') if line.startswith('data:') else line
        if content.startswith('think:'):
            continue
        out.append(content)
    return ''.join(out).replace('__CRLF__', '\n')


def stream_chat(base, token, text, timeout):
    """POST /v2/messages and record when each byte arrived (seconds since the request)."""
    body = json.dumps({'text': text}).encode()
    parts = urlsplit(base)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout)
    started = time.monotonic()
    conn.request(
        'POST',
        '/v2/messages',
        body=body,
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'X-Omi-Chat-Failure-Protocol': '1',
        },
    )
    resp = conn.getresponse()
    arrivals, raw = [], b''
    while True:
        try:
            chunk = resp.read1(65536)
        except (http.client.IncompleteRead, ConnectionError, TimeoutError) as error:
            arrivals.append((time.monotonic() - started, f'<{type(error).__name__}>'))
            break
        if not chunk:
            break
        raw += chunk
        arrivals.append((time.monotonic() - started, chunk.decode('utf-8', 'replace')))
    conn.close()
    lines = [line for line in raw.decode('utf-8', 'replace').split('\n') if line.strip()]
    return resp.status, dict(resp.getheaders()), arrivals, lines, time.monotonic() - started


def check_shipped_config(args):
    """The files compose mounts, exactly, pass nginx -t (they need nginx >= 1.27.3 for `resolve`)."""
    version = subprocess.run([args.nginx_bin, '-v'], capture_output=True, text=True).stderr
    match = re.search(r'nginx/(\d+)\.(\d+)\.(\d+)', version)
    if not match or tuple(map(int, match.groups())) < (1, 27, 3):
        ROWS.append(('config', 'shipped omi.conf + upstreams.conf + studio example pass nginx -t', 'SKIP',
                     f'{version.strip()} is older than 1.27.3 (the compose image is 1.30)'))
        return
    root = os.path.join(args.state, 'nginx-shipped')
    for sub in ('conf.d', 'omi/extra', 'logs'):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    proxy = args.proxy_dir
    with open(os.path.join(proxy, 'nginx', 'omi.conf')) as src, open(os.path.join(root, 'conf.d', 'default.conf'), 'w') as dst:
        dst.write(src.read())
    with open(os.path.join(proxy, 'nginx', 'upstreams.conf')) as src, open(os.path.join(root, 'omi', 'upstreams.conf'), 'w') as dst:
        dst.write(src.read())
    with open(os.path.join(proxy, 'nginx', 'extra', 'studio.conf.example')) as src, open(
        os.path.join(root, 'omi', 'extra', 'studio.conf'), 'w'
    ) as dst:
        dst.write(src.read())
    with open(os.path.join(root, 'nginx.conf'), 'w') as main:
        main.write('events {}\nhttp { include conf.d/*.conf; }\n')
    proc = subprocess.run(
        [args.nginx_bin, '-t', '-p', root, '-c', os.path.join(root, 'nginx.conf'), '-e', os.path.join(root, 'logs', 'error.log')],
        capture_output=True,
        text=True,
    )
    row('config', 'shipped omi.conf + upstreams.conf + studio example pass nginx -t', proc.returncode == 0,
        f'{version.strip()}: {proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else ""}')


def check_login(args, username, password):
    status, headers, body = request(
        args.proxy, 'POST', '/v1/auth/local-login', json.dumps({'username': username, 'password': password}),
        {'Content-Type': 'application/json'},
    )
    token = json.loads(body).get('session_token') if status == 200 else None
    row('login', 'POST /v1/auth/local-login through nginx', bool(token), f'HTTP {status}')
    status, _, _ = request(args.proxy, 'GET', '/v1/users/me/local-usage', headers={'Authorization': f'Bearer {token}'})
    row('login', 'an authenticated GET through nginx', status == 200, f'HTTP {status}')
    status, _, _ = request(args.proxy, 'GET', '/v1/users/me/local-usage')
    row('login', 'without a token the API still refuses (Omi\'s own auth stays on)', status in (401, 403), f'HTTP {status}')
    return token


def check_idle_cut(args):
    """Control: the idle-limited nginx really cuts a response that stays silent past its limit."""
    payload = json.dumps({'model': 'stub-model', 'stream': True, 'messages': [{'role': 'user', 'content': 'slowprobe'}]})
    started = time.monotonic()
    status, _, _ = request(args.idle_proxy, 'POST', '/__demo_llm/v1/chat/completions', payload,
                           {'Content-Type': 'application/json'}, timeout=args.llm_delay + 60)
    took = time.monotonic() - started
    row('idle limit', f'a silent model behind the {args.idle_seconds:.0f} s idle limit is cut (no keep-alive)',
        status == 504 and took < args.llm_delay, f'HTTP {status} after {took:.0f} s')


def check_chat(args, token):
    status, headers, arrivals, lines, took = stream_chat(args.proxy, token, 'When does the plumber come?', 120)
    lower = {k.lower(): v for k, v in headers.items()}
    row('chat', 'a quick answer streams through nginx (SSE, unbuffered)',
        status == 200 and lower.get('content-type', '').startswith('text/event-stream')
        and lower.get('x-accel-buffering') == 'no' and ANSWER.split()[0] in client_reply(lines),
        f'HTTP {status} in {took:.1f} s, {lower.get("content-type")}, X-Accel-Buffering={lower.get("x-accel-buffering")}')

    status, headers, arrivals, lines, took = stream_chat(
        args.idle_proxy, token, 'slowprobe: when does the plumber come?', args.llm_delay + 240
    )
    times = [t for t, _ in arrivals]
    gaps = [b - a for a, b in zip([0.0] + times, times)]
    max_gap = max(gaps) if gaps else float('inf')
    text = client_reply(lines)
    first_data = next((i for i, line in enumerate(lines) if line.startswith('data: ')), None)
    keepalives_before = lines[:first_data].count(HEARTBEAT) if first_data is not None else lines.count(HEARTBEAT)
    done = next((line for line in lines if line.startswith('done: ')), None)
    done_text = json.loads(base64.b64decode(done[6:]))['text'] if done else ''
    row('slow chat', f'model silent {args.llm_delay:.0f} s; the answer still arrives through a {args.idle_seconds:.0f} s idle limit',
        status == 200 and done is not None and ANSWER.split()[0] in done_text and took >= args.llm_delay,
        f'HTTP {status}, {took:.0f} s, done text: {done_text[:40]!r}')
    row('slow chat', f'keep-alive "{HEARTBEAT}" arrives before the first model event',
        keepalives_before >= int(args.llm_delay // args.keepalive_seconds) - 1,
        f'{keepalives_before} keep-alives before the first data: line')
    row('slow chat', f'no silence longer than the keep-alive interval ({args.keepalive_seconds:.0f} s) + 5 s',
        max_gap <= args.keepalive_seconds + 5, f'longest gap {max_gap:.1f} s over {len(arrivals)} reads')
    row('slow chat', 'client parse (app + desktop rules): reply text has no keep-alive in it',
        'Still working' not in text and ANSWER.split()[0] in text, repr(text[:60]))
    with open(os.path.join(args.state, 'slow-chat-arrivals.json'), 'w') as out:
        json.dump([[round(t, 2), c[:80]] for t, c in arrivals], out, indent=1)


def check_websocket(args, token):
    from websockets.exceptions import ConnectionClosed
    from websockets.sync.client import connect

    # The app's first settings call creates the user's profile, which /v4/listen requires.
    request(args.proxy, 'PATCH', '/v1/users/language', json.dumps({'language': 'en'}),
            {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
    parts = urlsplit(args.proxy)
    uri = f'ws://{parts.hostname}:{parts.port}/v4/listen?language=en&sample_rate=16000&codec=pcm16&channels=1&source=phone'
    try:
        with connect(uri, additional_headers={'Authorization': f'Bearer {token}'}, open_timeout=30) as ws:
            opened = ws.response.status_code if ws.response else None
            ws.send(b'\x00\x00' * 1600)  # 100 ms of silence, pcm16
            got = []
            # The demo has no speech service, so the session ends with 1011 after its status
            # frames; with one, the server also sends "ping" every 10 s (routers/listen/runtime.py).
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline and not (got and got[-1].startswith('<close')):
                try:
                    message = ws.recv(timeout=max(0.1, deadline - time.monotonic()))
                except TimeoutError:
                    break
                except ConnectionClosed as closed:
                    got.append(f'<close {closed.rcvd.code if closed.rcvd else None} {closed.rcvd.reason if closed.rcvd else ""}>')
                    break
                got.append(message if isinstance(message, str) else f'<{len(message)} bytes>')
        row('websocket', 'GET /v4/listen upgrades through nginx (101)', opened == 101, f'HTTP {opened}')
        row('websocket', 'the live session starts; its frames and close reach the phone through nginx',
            bool(got) and not got[0].startswith('<close'),
            f'{len(got)} frames: {got[0][:45] if got else ""} ... {got[-1][:40] if got else ""}')
    except Exception as error:  # noqa: BLE001 - the row records why
        row('websocket', 'GET /v4/listen upgrades through nginx (101)', False, f'{type(error).__name__}: {error}'[:120])


def dav(base, method, path, user, password, body=None, headers=None, host=None):
    auth = base64.b64encode(f'{user}:{password}'.encode()).decode()
    all_headers = {'Authorization': f'Basic {auth}', **(headers or {})}
    if host:
        all_headers['Host'] = host
    return request(base, method, path, body, all_headers)


def check_caldav(args):
    user, password = args.caldav_user, args.caldav_password
    status, _, body = dav(args.proxy, 'PROPFIND', f'/caldav/{user}/', user, password, headers={'Depth': '1'})
    hrefs = re.findall(rb'<(?:\w+:)?href>([^<]+)</(?:\w+:)?href>', body)
    row('caldav', 'PROPFIND /caldav/<user>/ through nginx (one hostname)',
        status == 207 and hrefs and all(h.startswith(b'/caldav/') for h in hrefs),
        f'HTTP {status}, {len(hrefs)} hrefs under /caldav/')
    cal = f'/caldav/{user}/proxy-demo-{uuid.uuid4().hex[:6]}/'
    mk = b'<?xml version="1.0"?><C:mkcalendar xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav"><D:set><D:prop><D:displayname>proxy demo</D:displayname></D:prop></D:set></C:mkcalendar>'
    s_mk, _, _ = dav(args.proxy, 'MKCALENDAR', cal, user, password, mk, {'Content-Type': 'application/xml'})
    uid = f'proxy-demo-{uuid.uuid4().hex}'
    ics = (
        'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//omi proxy demo//EN\r\nBEGIN:VEVENT\r\n'
        f'UID:{uid}\r\nDTSTAMP:20261125T090000Z\r\nDTSTART:20261127T090000Z\r\nDTEND:20261127T100000Z\r\n'
        'SUMMARY:Plumber\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'
    ).encode()
    s_put, _, _ = dav(args.proxy, 'PUT', cal + f'{uid}.ics', user, password, ics, {'Content-Type': 'text/calendar'})
    report = b'<?xml version="1.0"?><C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav"><D:prop><C:calendar-data/></D:prop><C:filter><C:comp-filter name="VCALENDAR"><C:comp-filter name="VEVENT"/></C:comp-filter></C:filter></C:calendar-query>'
    s_rep, _, rep = dav(args.proxy, 'REPORT', cal, user, password, report, {'Content-Type': 'application/xml', 'Depth': '1'})
    s_del, _, _ = dav(args.proxy, 'DELETE', cal, user, password)
    row('caldav', 'MKCALENDAR, PUT, REPORT, DELETE pass through nginx',
        s_mk == 201 and s_put in (201, 204) and s_rep == 207 and uid.encode() in rep and s_del in (200, 204),
        f'MKCALENDAR {s_mk}, PUT {s_put}, REPORT {s_rep}, DELETE {s_del}')
    status, _, _ = dav(args.proxy, 'PROPFIND', f'/{user}/', user, password, headers={'Depth': '0'}, host='caldav.example.com')
    row('caldav', 'PROPFIND on the caldav.<domain> hostname (Radicale at the root)', status == 207, f'HTTP {status}')
    status, headers, _ = request(args.proxy, 'GET', '/.well-known/caldav', headers={'X-Forwarded-Proto': 'https', 'Host': 'omi.example.com'})
    location = {k.lower(): v for k, v in headers.items()}.get('location')
    row('caldav', '/.well-known/caldav points apps at /caldav/', status == 301 and location == 'https://omi.example.com/caldav/',
        f'HTTP {status} -> {location}')


def multipart_upload(base, path, token, size, filename):
    boundary = uuid.uuid4().hex
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{filename}"\r\n'
        'Content-Type: application/octet-stream\r\n\r\n'
    ).encode()
    tail = f'\r\n--{boundary}--\r\n'.encode()
    frame = (160).to_bytes(4, 'little') + os.urandom(160)
    block = frame * (1024 * 1024 // len(frame))

    def body():
        yield head
        sent = 0
        while sent < size:
            piece = block[: min(len(block), size - sent)]
            sent += len(piece)
            yield piece
        yield tail

    conn = connection(base, timeout=300)
    started = time.monotonic()
    try:
        conn.request('POST', path, body=body(), headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': f'multipart/form-data; boundary={boundary}',
            'Content-Length': str(len(head) + size + len(tail)),
        })
        resp = conn.getresponse()
        return resp.status, resp.read()[:300], time.monotonic() - started
    except (BrokenPipeError, ConnectionResetError):
        resp = conn.getresponse()
        return resp.status, resp.read()[:300], time.monotonic() - started
    finally:
        conn.close()


def check_upload(args, token):
    size = args.upload_mb * 1024 * 1024
    name = f'audio_omidemo_opus_16000_1_fs160_{int(time.time()) - 60}.bin'
    status, body, took = multipart_upload(args.proxy, '/v2/sync-local-files', token, size, name)
    from_backend = body.lstrip().startswith(b'{')
    row('upload', f'{args.upload_mb} MB audio sync upload (over Cloudflare\'s 100 MB) passes nginx',
        status in (200, 202) and from_backend, f'HTTP {status} in {took:.1f} s: {body[:90]!r}')
    status, body, _ = multipart_upload(args.proxy, '/v2/sync-local-files', token, 230 * 1024 * 1024, name)
    row('upload', 'over client_max_body_size (230 MB) nginx answers 413 itself', status == 413, f'HTTP {status}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--backend', required=True)
    parser.add_argument('--caldav', required=True)
    parser.add_argument('--caldav-user', required=True)
    parser.add_argument('--caldav-password', required=True)
    parser.add_argument('--seed-cmd', required=True)
    parser.add_argument('--proxy', required=True)
    parser.add_argument('--idle-proxy', required=True)
    parser.add_argument('--idle-seconds', type=float, required=True)
    parser.add_argument('--keepalive-seconds', type=float, required=True)
    parser.add_argument('--llm-delay', type=float, required=True)
    parser.add_argument('--upload-mb', type=int, default=150)
    parser.add_argument('--nginx-bin', required=True)
    parser.add_argument('--proxy-dir', required=True)
    parser.add_argument('--state', required=True)
    args, _unknown = parser.parse_known_args()  # caldav_demo.sh's own (--allglance-url, ...)

    username, password = 'remote-demo', 'remote-demo-password-123'
    seed_account(args.seed_cmd, username, password)
    check_shipped_config(args)
    token = check_login(args, username, password)
    if token:
        check_chat(args, token)
        check_websocket(args, token)
        check_upload(args, token)
    check_caldav(args)
    check_idle_cut(args)

    width = max(len(r[1]) for r in ROWS)
    print(f'\n{"area":<11} {"check":<{width}}  result  detail')
    for area, check, result, detail in ROWS:
        print(f'{area:<11} {check:<{width}}  {result:<6}  {detail}')
    failed = sum(1 for r in ROWS if r[2] == 'FAIL')
    print(f'\n{len(ROWS) - failed} passed or skipped, {failed} failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
