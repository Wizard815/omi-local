// The user's own omi-local server, and the session this app holds with it.
//
// omi-local is self-hosted: there is no Omi cloud and no Firebase project. The
// app signs in with a username and password at the server's
// POST /v1/auth/local-login, which answers with a session token the server signs
// itself (backend/utils/local_auth.py: HS256, 30-day expiry, no refresh). Every
// API call sends that token as `Authorization: Bearer <token>`.
//
// Main owns both the server URL and the session:
//   - the URL is the user's choice (login screen or Settings), so it is runtime
//     config in userData, not a build-time constant;
//   - the token is fetched by main over its own connection to that URL, so main
//     can trust the `uid` claim in it without the server's signing key. A token a
//     renderer merely relays is trusted only when it is exactly this one
//     (see `uidForRelayedToken`).
// The session is kept encrypted with Electron safeStorage when the OS offers it
// (DPAPI on Windows, the Secret Service/kwallet on Linux); without it, a file only
// the user can read.
import { chmodSync, existsSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { decodeJwtClaims } from '../auth/omiAuth'
import {
  LOCAL_LOGIN_PATH,
  normalizeServerUrl,
  loginErrorMessage,
  type OmiLocalSession,
  type OmiLocalState,
  type OmiLocalLoginResult,
  type OmiLocalServerCheck
} from '../../shared/omiLocal'

/** Where the app looks before the user has chosen a server: the host's own
 *  backend on the omi-host default port. */
export const DEFAULT_SERVER_URL = 'http://localhost:8000'

type Crypto = {
  isEncryptionAvailable: () => boolean
  encryptString: (s: string) => Buffer
  decryptString: (b: Buffer) => string
}

export type ServerSessionDeps = {
  userDataDir: string
  crypto: Crypto | null
  fetchImpl?: typeof fetch
  /** OMI_SERVER_URL: pins the server (harnesses, managed installs). */
  envServerUrl?: string
  now?: () => number
}

type StoredSession = OmiLocalSession & { serverUrl: string }

export class ServerSession {
  private serverUrl: string
  private session: StoredSession | null
  private listeners = new Set<(state: OmiLocalState) => void>()

  constructor(private deps: ServerSessionDeps) {
    this.serverUrl = this.loadServerUrl()
    this.session = this.loadSession()
  }

  private get configPath(): string {
    return join(this.deps.userDataDir, 'omi-local-server.json')
  }

  private get sessionPath(): string {
    return join(this.deps.userDataDir, 'omi-local-session.bin')
  }

  private now(): number {
    return (this.deps.now ?? Date.now)()
  }

  private loadServerUrl(): string {
    const pinned = this.deps.envServerUrl ? normalizeServerUrl(this.deps.envServerUrl) : null
    if (pinned) return pinned
    try {
      const raw = JSON.parse(readFileSync(this.configPath, 'utf8')) as { serverUrl?: unknown }
      const saved = typeof raw.serverUrl === 'string' ? normalizeServerUrl(raw.serverUrl) : null
      if (saved) return saved
    } catch {
      /* first run */
    }
    return DEFAULT_SERVER_URL
  }

  private loadSession(): StoredSession | null {
    if (!existsSync(this.sessionPath)) return null
    try {
      const bytes = readFileSync(this.sessionPath)
      const crypto = this.deps.crypto
      const text =
        bytes[0] === 0x7b /* '{' plaintext */ || !crypto?.isEncryptionAvailable()
          ? bytes.toString('utf8')
          : crypto.decryptString(bytes)
      const parsed = JSON.parse(text) as Partial<StoredSession>
      if (
        typeof parsed.token !== 'string' ||
        typeof parsed.uid !== 'string' ||
        typeof parsed.serverUrl !== 'string'
      ) {
        return null
      }
      // A session belongs to the server that issued it.
      if (parsed.serverUrl !== this.serverUrl) return null
      return {
        token: parsed.token,
        uid: parsed.uid,
        username: typeof parsed.username === 'string' ? parsed.username : '',
        expiresAt: typeof parsed.expiresAt === 'number' ? parsed.expiresAt : null,
        serverUrl: parsed.serverUrl
      }
    } catch {
      return null
    }
  }

  private saveSession(session: StoredSession | null): void {
    if (!session) {
      rmSync(this.sessionPath, { force: true })
      return
    }
    const text = JSON.stringify(session)
    const crypto = this.deps.crypto
    const bytes = crypto?.isEncryptionAvailable()
      ? crypto.encryptString(text)
      : Buffer.from(text, 'utf8')
    writeFileSync(this.sessionPath, bytes, { mode: 0o600 })
    try {
      chmodSync(this.sessionPath, 0o600)
    } catch {
      /* Windows: ACLs, not modes */
    }
  }

  private saveServerUrl(): void {
    writeFileSync(this.configPath, JSON.stringify({ serverUrl: this.serverUrl }, null, 2))
  }

  /** Server URL and session (token included: the renderer sends it on its API calls). */
  state(): OmiLocalState {
    const s = this.session
    const expired = s?.expiresAt != null && s.expiresAt <= this.now()
    return {
      serverUrl: this.serverUrl,
      serverUrlPinned: !!(this.deps.envServerUrl && normalizeServerUrl(this.deps.envServerUrl)),
      session:
        s && !expired
          ? { token: s.token, uid: s.uid, username: s.username, expiresAt: s.expiresAt }
          : null
    }
  }

  getServerUrl(): string {
    return this.serverUrl
  }

  /** The signed-in user's token for main's own API calls, or null. */
  getToken(): string | null {
    return this.state().session?.token ?? null
  }

  /** The uid of a token a renderer relayed, only when it is the session main holds. */
  uidForRelayedToken(token: string | null | undefined): string | null {
    const s = this.state().session
    return s && token && token === s.token ? s.uid : null
  }

  onChange(cb: (state: OmiLocalState) => void): () => void {
    this.listeners.add(cb)
    return () => this.listeners.delete(cb)
  }

  private emit(): void {
    const state = this.state()
    for (const cb of this.listeners) cb(state)
  }

  /** Change the server. A different server means a different account: sign out. */
  setServerUrl(raw: string): { ok: true; state: OmiLocalState } | { ok: false; error: string } {
    const next = normalizeServerUrl(raw)
    if (!next) return { ok: false, error: 'Enter a server address like http://192.168.1.10:8000' }
    if (this.state().serverUrlPinned && next !== this.serverUrl) {
      return { ok: false, error: 'The server is set by OMI_SERVER_URL for this install' }
    }
    if (next !== this.serverUrl) {
      this.serverUrl = next
      this.saveServerUrl()
      this.session = null
      this.saveSession(null)
      this.emit()
    }
    return { ok: true, state: this.state() }
  }

  /** GET /health on a server, to tell "wrong address" from "wrong password". */
  async checkServer(raw: string): Promise<OmiLocalServerCheck> {
    const url = normalizeServerUrl(raw)
    if (!url) return { ok: false, error: 'Enter a server address like http://192.168.1.10:8000' }
    const fetchImpl = this.deps.fetchImpl ?? fetch
    try {
      const res = await fetchImpl(`${url}/health`, { signal: AbortSignal.timeout(8_000) })
      if (!res.ok) return { ok: false, error: `The server answered ${res.status}` }
      return { ok: true, serverUrl: url }
    } catch {
      return { ok: false, error: `Can't reach ${url}` }
    }
  }

  async login(rawUrl: string, username: string, password: string): Promise<OmiLocalLoginResult> {
    const url = normalizeServerUrl(rawUrl)
    if (!url) return { ok: false, error: 'Enter a server address like http://192.168.1.10:8000' }
    if (!username.trim() || !password) return { ok: false, error: 'Enter your username and password' }
    if (this.state().serverUrlPinned && url !== this.serverUrl) {
      return { ok: false, error: 'The server is set by OMI_SERVER_URL for this install' }
    }
    const fetchImpl = this.deps.fetchImpl ?? fetch
    let res: Response
    try {
      res = await fetchImpl(`${url}${LOCAL_LOGIN_PATH}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username.trim(), password }),
        signal: AbortSignal.timeout(30_000)
      })
    } catch {
      return { ok: false, error: `Can't reach ${url}` }
    }
    if (!res.ok) return { ok: false, error: loginErrorMessage(res.status) }
    let token = ''
    try {
      const body = (await res.json()) as { session_token?: unknown }
      token = typeof body.session_token === 'string' ? body.session_token : ''
    } catch {
      /* handled below */
    }
    const claims = token ? decodeJwtClaims(token) : null
    const uid = claims && typeof claims.uid === 'string' ? claims.uid : ''
    if (!uid) return { ok: false, error: 'The server sent an answer this app does not understand' }
    const exp = claims && typeof claims.exp === 'number' ? claims.exp * 1000 : null
    if (url !== this.serverUrl) {
      this.serverUrl = url
      this.saveServerUrl()
    } else if (!existsSync(this.configPath)) {
      this.saveServerUrl()
    }
    this.session = { token, uid, username: username.trim(), expiresAt: exp, serverUrl: url }
    this.saveSession(this.session)
    this.emit()
    return { ok: true, state: this.state() }
  }

  signOut(): OmiLocalState {
    this.session = null
    this.saveSession(null)
    this.emit()
    return this.state()
  }
}
