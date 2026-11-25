// Shared shapes and rules for the user's omi-local server (main + renderer).
// See src/main/omiLocal/serverSession.ts for the design.

/** The username/password sign-in of omi-local (backend/routers/auth.py). */
export const LOCAL_LOGIN_PATH = '/v1/auth/local-login'

export type OmiLocalSession = {
  /** Session token the server signed; sent as `Authorization: Bearer`. */
  token: string
  uid: string
  username: string
  /** Epoch ms the token stops working (30 days after sign-in), or null. */
  expiresAt: number | null
}

export type OmiLocalState = {
  /** Origin (+ optional path prefix) of the server, no trailing slash. */
  serverUrl: string
  /** True when OMI_SERVER_URL fixes the server for this install. */
  serverUrlPinned: boolean
  session: OmiLocalSession | null
}

export type OmiLocalLoginResult = { ok: true; state: OmiLocalState } | { ok: false; error: string }

export type OmiLocalServerCheck = { ok: true; serverUrl: string } | { ok: false; error: string }

/**
 * `192.168.1.10:8000`, `http://omi.lan:8000/`, `https://omi.example.org` → a
 * clean base URL without a trailing slash; null for anything that is not an
 * http(s) address. A bare host gets http:// (a LAN server rarely has TLS).
 */
export function normalizeServerUrl(raw: string): string | null {
  const trimmed = raw.trim()
  if (!trimmed) return null
  const withScheme = /^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed) ? trimmed : `http://${trimmed}`
  let url: URL
  try {
    url = new URL(withScheme)
  } catch {
    return null
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return null
  if (!url.hostname || url.username || url.password) return null
  const path = url.pathname.replace(/\/+$/, '')
  return `${url.protocol}//${url.host}${path}`
}

/** The websocket origin of a server URL (http → ws, https → wss). */
export function serverWebSocketUrl(serverUrl: string): string {
  return serverUrl.replace(/^http/i, 'ws')
}

/** What the login screen says for a refused sign-in. The server answers every
 *  wrong username or password alike, on purpose (utils/local_auth.py). */
export function loginErrorMessage(status: number): string {
  if (status === 401 || status === 403) return 'Wrong username or password'
  if (status === 429) return 'Too many tries. Wait 15 minutes, then try again'
  if (status === 404) return 'This server has no omi-local sign-in. Check the address'
  if (status === 422) return 'Enter your username and password'
  return `The server could not sign you in (${status})`
}
