// The renderer pages' Content-Security-Policy, built from the user's server.
//
// connect-src is the guard behind "nothing leaves for anyone but the user's own
// server": a page may open HTTP(S)/WebSocket connections to that server and to
// loopback (the app's own helpers and dev server), nothing else. There is no
// analytics, crash-report, Firebase or Omi-cloud host in it; a stray third-party
// call from the renderer fails here even if one were reintroduced.
//
// Images stay open to https: (conversation and app icons, profile pictures some
// servers link to). The renderer HTML files carry no CSP of their own any more:
// two policies would intersect, and a static one cannot know the server.
import { serverWebSocketUrl } from '../../shared/omiLocal'

const LOOPBACK = [
  'http://localhost:*',
  'http://127.0.0.1:*',
  'ws://localhost:*',
  'ws://127.0.0.1:*'
]

export function buildRendererCsp(serverUrl: string): string {
  let origin = ''
  try {
    origin = new URL(serverUrl).origin
  } catch {
    origin = ''
  }
  const server = origin ? [origin, serverWebSocketUrl(origin)] : []
  const connect = ["'self'", ...server, ...LOOPBACK, 'blob:', 'data:']
  return [
    "default-src 'self'",
    "script-src 'self' 'unsafe-eval' 'wasm-unsafe-eval' blob:",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob: https: " + server.join(' '),
    "font-src 'self' data:",
    "media-src 'self' blob: data: " + server.join(' '),
    `connect-src ${connect.join(' ')}`,
    "frame-src 'self'",
    "worker-src 'self' blob:"
  ]
    .map((d) => d.trim())
    .join('; ')
}
