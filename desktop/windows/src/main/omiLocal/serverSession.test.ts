import { mkdtempSync, readFileSync, rmSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ServerSession, DEFAULT_SERVER_URL } from './serverSession'
import { buildRendererCsp } from './csp'
import { normalizeServerUrl } from '../../shared/omiLocal'

function token(claims: Record<string, unknown>): string {
  const b64 = (o: unknown): string => Buffer.from(JSON.stringify(o)).toString('base64url')
  return `${b64({ alg: 'HS256', typ: 'JWT' })}.${b64(claims)}.sig`
}

const NOW = 1_800_000_000_000
const fakeCrypto = {
  isEncryptionAvailable: (): boolean => true,
  encryptString: (s: string): Buffer => Buffer.from(`enc:${s}`, 'utf8'),
  decryptString: (b: Buffer): string => b.toString('utf8').replace(/^enc:/, '')
}

let dir: string
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'omi-local-session-'))
})
afterEach(() => rmSync(dir, { recursive: true, force: true }))

function loginFetch(status: number, body: unknown): ReturnType<typeof vi.fn> {
  return vi.fn(async () => new Response(JSON.stringify(body), { status }))
}

describe('normalizeServerUrl', () => {
  it('turns what people type into a base URL', () => {
    expect(normalizeServerUrl('192.0.2.10:8000')).toBe('http://192.0.2.10:8000')
    expect(normalizeServerUrl(' http://omi.lan:8000/ ')).toBe('http://omi.lan:8000')
    expect(normalizeServerUrl('https://omi.example.org/api/')).toBe('https://omi.example.org/api')
  })

  it('refuses anything that is not an http(s) address', () => {
    expect(normalizeServerUrl('')).toBeNull()
    expect(normalizeServerUrl('ftp://omi.lan')).toBeNull()
    expect(normalizeServerUrl('http://user:pw@omi.lan')).toBeNull()
  })
})

describe('ServerSession', () => {
  it('starts on the default server, signed out', () => {
    const s = new ServerSession({ userDataDir: dir, crypto: fakeCrypto })
    expect(s.state()).toEqual({ serverUrl: DEFAULT_SERVER_URL, serverUrlPinned: false, session: null })
  })

  it('signs in at /v1/auth/local-login and keeps the uid from the token it fetched', async () => {
    const t = token({ uid: 'uid-dana', exp: NOW / 1000 + 3600 })
    const fetchImpl = loginFetch(200, { session_token: t })
    const s = new ServerSession({ userDataDir: dir, crypto: fakeCrypto, fetchImpl, now: () => NOW })
    const result = await s.login('192.0.2.10:8000', ' dana ', 'pw')
    expect(fetchImpl).toHaveBeenCalledWith(
      'http://192.0.2.10:8000/v1/auth/local-login',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ username: 'dana', password: 'pw' }) })
    )
    expect(result.ok).toBe(true)
    expect(s.state().serverUrl).toBe('http://192.0.2.10:8000')
    expect(s.state().session).toEqual({
      token: t,
      uid: 'uid-dana',
      username: 'dana',
      expiresAt: NOW + 3_600_000
    })
    expect(s.uidForRelayedToken(t)).toBe('uid-dana')
    expect(s.uidForRelayedToken(token({ uid: 'uid-dana' }))).toBeNull()
  })

  it('stores the session encrypted, owner-only, and reloads it', async () => {
    const t = token({ uid: 'u1', exp: NOW / 1000 + 60 })
    const s = new ServerSession({
      userDataDir: dir,
      crypto: fakeCrypto,
      fetchImpl: loginFetch(200, { session_token: t }),
      now: () => NOW
    })
    await s.login('http://omi.lan:8000', 'dana', 'pw')
    const raw = readFileSync(join(dir, 'omi-local-session.bin'), 'utf8')
    expect(raw.startsWith('enc:')).toBe(true)
    if (process.platform !== 'win32') {
      expect(statSync(join(dir, 'omi-local-session.bin')).mode & 0o777).toBe(0o600)
    }
    const again = new ServerSession({ userDataDir: dir, crypto: fakeCrypto, now: () => NOW })
    expect(again.state().serverUrl).toBe('http://omi.lan:8000')
    expect(again.state().session?.uid).toBe('u1')
  })

  it('reports an expired session as signed out', async () => {
    const t = token({ uid: 'u1', exp: NOW / 1000 - 1 })
    const s = new ServerSession({
      userDataDir: dir,
      crypto: fakeCrypto,
      fetchImpl: loginFetch(200, { session_token: t }),
      now: () => NOW
    })
    await s.login('http://omi.lan:8000', 'dana', 'pw')
    expect(s.state().session).toBeNull()
    expect(s.getToken()).toBeNull()
  })

  it('explains a refused or unreachable sign-in', async () => {
    const wrong = new ServerSession({ userDataDir: dir, crypto: fakeCrypto, fetchImpl: loginFetch(401, {}) })
    expect(await wrong.login('omi.lan:8000', 'dana', 'x')).toEqual({
      ok: false,
      error: 'Wrong username or password'
    })
    const limited = new ServerSession({ userDataDir: dir, crypto: fakeCrypto, fetchImpl: loginFetch(429, {}) })
    expect(await limited.login('omi.lan:8000', 'dana', 'x')).toMatchObject({ ok: false })
    const down = new ServerSession({
      userDataDir: dir,
      crypto: fakeCrypto,
      fetchImpl: vi.fn(async () => {
        throw new TypeError('fetch failed')
      })
    })
    expect(await down.login('omi.lan:8000', 'dana', 'x')).toEqual({
      ok: false,
      error: "Can't reach http://omi.lan:8000"
    })
    expect(down.state().session).toBeNull()
  })

  it('signs out when the server changes, and notifies listeners', async () => {
    const t = token({ uid: 'u1' })
    const s = new ServerSession({
      userDataDir: dir,
      crypto: fakeCrypto,
      fetchImpl: loginFetch(200, { session_token: t })
    })
    await s.login('http://a.lan:8000', 'dana', 'pw')
    const seen: Array<string | null> = []
    s.onChange((st) => seen.push(st.session?.uid ?? null))
    expect(s.setServerUrl('http://b.lan:8000')).toMatchObject({ ok: true })
    expect(s.state().session).toBeNull()
    expect(seen).toEqual([null])
    expect(s.setServerUrl('nonsense://')).toMatchObject({ ok: false })
  })

  it('honours OMI_SERVER_URL and refuses to move off it', async () => {
    const s = new ServerSession({ userDataDir: dir, crypto: fakeCrypto, envServerUrl: 'omi.box:8000' })
    expect(s.state()).toMatchObject({ serverUrl: 'http://omi.box:8000', serverUrlPinned: true })
    expect(s.setServerUrl('http://other:8000')).toMatchObject({ ok: false })
    expect(await s.login('http://other:8000', 'dana', 'pw')).toMatchObject({ ok: false })
  })
})

describe('buildRendererCsp', () => {
  it('lets pages connect to the server and loopback only — no analytics or cloud hosts', () => {
    const csp = buildRendererCsp('http://192.0.2.10:8000')
    const connect = csp.split('; ').find((d) => d.startsWith('connect-src')) ?? ''
    expect(connect).toContain('http://192.0.2.10:8000')
    expect(connect).toContain('ws://192.0.2.10:8000')
    expect(connect).not.toMatch(/posthog|sentry|googleapis|firebase|omi\.me|openai|deepgram|https:\s/)
    expect(csp).not.toMatch(/posthog|sentry|segment|mixpanel|amplitude|google-analytics/)
  })
})
