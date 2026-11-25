// The renderer's view of the omi-local session.
//
// omi-local has no Firebase: the user signs in with a username and password on
// their own server, and main keeps the session (src/main/omiLocal). This module
// mirrors it for the renderer with the small surface the app used from the
// Firebase Auth SDK — `auth.currentUser`, `onAuthStateChanged`,
// `onIdTokenChanged`, `getIdToken()` — so every API client sends the session
// token as `Authorization: Bearer` and reacts to sign-in/out the same way.
//
// The state arrives synchronously from the preload (main answers a sendSync at
// window load), so the first render already knows the server and the user, and
// every later change (sign-in, sign-out, server change) is pushed by main to all
// windows at once.
import type { OmiLocalLoginResult, OmiLocalState } from '../../../shared/omiLocal'

export type SessionUser = {
  readonly uid: string
  readonly username: string
  /** The name the user gave in onboarding (kept on this device), else the username. */
  readonly displayName: string | null
  readonly email: string | null
  readonly photoURL: string | null
  /**
   * The session token. There is no refresh: the server's token lasts 30 days.
   * An expired token rejects with Firebase's `auth/user-token-expired` code so
   * the API clients' refresh→retry→reauth path (authSession.ts) routes to
   * sign-in.
   */
  getIdToken: (forceRefresh?: boolean) => Promise<string>
}

/** What `auth` stood for in the Firebase SDK: holds the current user. */
export type SessionAuth = {
  readonly currentUser: SessionUser | null
  /** Resolves once the persisted session is known: at once (main sent it with
   *  the preload), kept for the windows that wait on it. */
  authStateReady: () => Promise<void>
}

type Listener = (user: SessionUser | null) => void

const DEFAULT_STATE: OmiLocalState = {
  serverUrl: 'http://localhost:8000',
  serverUrlPinned: false,
  session: null
}

const DISPLAY_NAME_KEY = 'omi-local:display-name:'

function readInitialState(): OmiLocalState {
  if (typeof window === 'undefined') return DEFAULT_STATE
  try {
    return window.omi?.omiLocalInitialState ?? DEFAULT_STATE
  } catch {
    return DEFAULT_STATE
  }
}

let state: OmiLocalState = readInitialState()
let currentUser: SessionUser | null = null
const authListeners = new Set<Listener>()
const tokenListeners = new Set<Listener>()

function storedDisplayName(uid: string): string | null {
  try {
    return localStorage.getItem(DISPLAY_NAME_KEY + uid)
  } catch {
    return null
  }
}

function userFrom(s: OmiLocalState): SessionUser | null {
  const session = s.session
  if (!session) return null
  return {
    uid: session.uid,
    username: session.username,
    get displayName(): string | null {
      return storedDisplayName(session.uid) ?? (session.username || null)
    },
    email: null,
    photoURL: null,
    getIdToken: async () => {
      if (session.expiresAt != null && session.expiresAt <= Date.now()) {
        const error = new Error('Your session on this server has expired') as Error & {
          code: string
        }
        error.code = 'auth/user-token-expired'
        throw error
      }
      return session.token
    }
  }
}

function apply(next: OmiLocalState): void {
  const prevUid = currentUser?.uid ?? null
  const prevToken = state.session?.token ?? null
  state = next
  currentUser = userFrom(next)
  const uid = currentUser?.uid ?? null
  const token = next.session?.token ?? null
  if (uid !== prevUid) for (const cb of [...authListeners]) cb(currentUser)
  if (token !== prevToken || uid !== prevUid) for (const cb of [...tokenListeners]) cb(currentUser)
}

currentUser = userFrom(state)

if (typeof window !== 'undefined') {
  try {
    window.omi?.onOmiLocalChanged?.((next) => apply(next))
  } catch {
    /* tests / no preload */
  }
}

export const auth: SessionAuth = {
  get currentUser(): SessionUser | null {
    return currentUser
  },
  authStateReady: () => Promise.resolve()
}

/** Base URL of the user's omi-local server (no trailing slash). Every API client
 *  resolves it per request, so a server change applies without a rebuild. */
export function serverUrl(): string {
  return state.serverUrl
}

export function serverUrlPinned(): boolean {
  return state.serverUrlPinned
}

/** Fires now with the current user, then on every sign-in / sign-out. */
export function onAuthStateChanged(_auth: SessionAuth, cb: Listener): () => void {
  authListeners.add(cb)
  queueMicrotask(() => {
    if (authListeners.has(cb)) cb(currentUser)
  })
  return () => authListeners.delete(cb)
}

/** Fires now, then whenever the user or their token changes. */
export function onIdTokenChanged(_auth: SessionAuth, cb: Listener): () => void {
  tokenListeners.add(cb)
  queueMicrotask(() => {
    if (tokenListeners.has(cb)) cb(currentUser)
  })
  return () => tokenListeners.delete(cb)
}

/** Sign in on a server (main stores the server URL and the session). */
export async function signIn(
  server: string,
  username: string,
  password: string
): Promise<OmiLocalLoginResult> {
  const result = await window.omi.omiLocalLogin(server, username, password)
  if (result.ok) apply(result.state)
  return result
}

/** LIGHT sign-out: drop the session, keep local data (a dead session's 401). */
export async function signOut(_auth?: SessionAuth): Promise<void> {
  apply(await window.omi.omiLocalSignOut())
}

/** Keep the user's chosen name on this device (the server has no name field). */
export async function updateProfile(
  user: SessionUser,
  profile: { displayName?: string | null }
): Promise<void> {
  try {
    if (profile.displayName) localStorage.setItem(DISPLAY_NAME_KEY + user.uid, profile.displayName)
    else localStorage.removeItem(DISPLAY_NAME_KEY + user.uid)
  } catch {
    /* cosmetic only */
  }
}

/** Test seam: replace the mirrored state as main would push it. */
export function __setSessionStateForTest(next: OmiLocalState): void {
  apply(next)
}
