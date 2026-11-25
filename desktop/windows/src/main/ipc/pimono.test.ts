// pimono:setSession handler — the seam that wires the control-plane owner to the
// signed-in user, HOST-DERIVED: the relayed token counts only when it is exactly
// the omi-local session token main fetched itself (omiLocal/serverSession.ts).
//
// This is the regression suite for the cross-account data-sharing fix: the owner
// keys every kernel session/surface row (surfaceSession.ts), and before this
// wiring it stayed the shared DEFAULT_LOCAL_OWNER_ID constant for every account —
// so two accounts under one Windows profile collided on the same rows. The owner
// must now come from the token's VERIFIED `sub` (not a renderer-asserted field, and
// not a mere decode a forged unsigned token could spoof), switch per account, and
// reset to the default on sign-out.
//
// main's session (omiLocal) is MOCKED here so this suite tests the WIRING —
// session-check result → owner; ServerSession.uidForRelayedToken has its own suite
// (omiLocal/serverSession.test.ts). The kernel's heavy collaborators
// are mocked too (same pattern as controlPlane.pimono.test.ts); the owner functions
// under test are the REAL ones.

import { mkdtempSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const dir = mkdtempSync(join(tmpdir(), 'omi-pimono-ipc-'))
const handlers = new Map<string, (event: unknown, ...args: unknown[]) => unknown>()

vi.mock('electron', () => ({
  app: { getPath: (): string => dir },
  ipcMain: {
    handle: (channel: string, fn: (event: unknown, ...args: unknown[]) => unknown): void => {
      handlers.set(channel, fn)
    }
  },
  BrowserWindow: { getAllWindows: (): unknown[] => [] },
  webContents: { getAllWebContents: (): unknown[] => [] }
}))
vi.mock('../agentKernel/store', () => ({ SqliteAgentStore: class {} }))
vi.mock('../agentKernel/kernel', () => ({ AgentRuntimeKernel: class {} }))
vi.mock('../agentKernel/controlMcpBridge', () => ({
  AgentControlMcpBridge: class {
    start = vi.fn(() => Promise.resolve())
    close = vi.fn(() => Promise.resolve())
    register = vi.fn(() => ({ pipePath: 'p', token: 't' }))
  }
}))
vi.mock('../agentKernel/toolRelayBridge', () => ({
  AgentToolRelayBridge: class {
    start = vi.fn(() => Promise.resolve())
    close = vi.fn(() => Promise.resolve())
  }
}))
vi.mock('../codingAgent/piMono', () => ({
  PiMonoAdapter: vi.fn(function (this: Record<string, unknown>) {
    this.updateAuthToken = vi.fn(() => Promise.resolve(true))
    this.updateByokEnv = vi.fn(() => Promise.resolve(true))
    this.revokeAndStop = vi.fn(() => Promise.resolve())
  }),
  PiMonoRuntimeAdapter: vi.fn(function (this: Record<string, unknown>) {
    this.adapterId = 'pi-mono'
  })
}))
// main's own session: mocked so the wiring test controls "genuine vs forged".
const uidForRelayedToken = vi.hoisted(() => vi.fn((_token: string): string | null => null))
vi.mock('../omiLocal', () => ({
  serverSession: () => ({ uidForRelayedToken })
}))

import { registerPiMonoHandlers } from './pimono'
import {
  controlPlaneOwnerId,
  hasKnownControlPlaneOwner,
  resetControlPlaneForTests
} from '../agentKernel/controlPlane'
import { DEFAULT_LOCAL_OWNER_ID } from '../agentKernel/controlTools'
import {
  __resetPiMonoSessionForTests,
  __setByokKeyStoreForTests
} from '../codingAgent/piMonoSession'
import type { ByokKeyStore } from '../agentKernel/byokStore'

const verify = uidForRelayedToken
const noByok = { getAllKeys: () => ({}) } as unknown as ByokKeyStore

async function setSession(payload: unknown): Promise<void> {
  const handler = handlers.get('pimono:setSession')
  if (!handler) throw new Error('pimono:setSession was not registered')
  await handler({}, payload)
}

const base = 'http://omi.lan:8000'

afterAll(() => rmSync(dir, { recursive: true, force: true }))

beforeEach(() => {
  handlers.clear()
  verify.mockReset()
  verify.mockReturnValue(null)
  resetControlPlaneForTests()
  __resetPiMonoSessionForTests()
  __setByokKeyStoreForTests(noByok)
  registerPiMonoHandlers()
})

afterEach(() => {
  resetControlPlaneForTests()
  __resetPiMonoSessionForTests()
})

describe('pimono:setSession — control-plane owner wiring', () => {
  it('starts at the default constant with no known owner before any relay', () => {
    expect(controlPlaneOwnerId()).toBe(DEFAULT_LOCAL_OWNER_ID)
    expect(hasKnownControlPlaneOwner()).toBe(false)
  })

  it("sets the owner to the session's uid when the relayed token is main's own", async () => {
    verify.mockReturnValue('uid-A')
    await setSession({ token: 'genuine-A', desktopApiBase: base })
    expect(verify).toHaveBeenCalledWith('genuine-A')
    expect(controlPlaneOwnerId()).toBe('uid-A')
    expect(hasKnownControlPlaneOwner()).toBe(true)
  })

  it('resets the owner to the default on sign-out (null session)', async () => {
    verify.mockReturnValue('uid-A')
    await setSession({ token: 'genuine-A', desktopApiBase: base })
    await setSession(null)
    expect(controlPlaneOwnerId()).toBe(DEFAULT_LOCAL_OWNER_ID)
    expect(hasKnownControlPlaneOwner()).toBe(false)
  })

  it('switches the owner when a second account signs in — no stale first-account owner', async () => {
    // The account-switch case the fix exists for: signing out A then in as B must
    // scope subsequent kernel work to B, never A and never the shared constant.
    verify.mockReturnValue('account-A')
    await setSession({ token: 'genuine-A', desktopApiBase: base })
    expect(controlPlaneOwnerId()).toBe('account-A')
    await setSession(null)
    verify.mockReturnValue('account-B')
    await setSession({ token: 'genuine-B', desktopApiBase: base })
    expect(controlPlaneOwnerId()).toBe('account-B')
  })

  it("is host-authoritative — a token that is not main's session falls back to default, not a leaked owner", async () => {
    // A valid session SHAPE with a token the verifier rejects (bad sig / unsigned /
    // wrong alg / expired / wrong aud-iss): verify → null, so the owner stays the
    // default constant. hasKnownControlPlaneOwner() is false, which the cold-start
    // gate uses to refuse — fail closed, never key rows under a forged owner. This
    // is the core of the access-control fix.
    verify.mockReturnValue(null)
    await setSession({ token: 'forged-victim-uid', desktopApiBase: base })
    expect(verify).toHaveBeenCalledWith('forged-victim-uid')
    expect(controlPlaneOwnerId()).toBe(DEFAULT_LOCAL_OWNER_ID)
    expect(hasKnownControlPlaneOwner()).toBe(false)
  })

  it('does not even attempt verification for an invalid (coerced-to-null) session payload', async () => {
    await setSession({ token: '', desktopApiBase: base }) // coerces to null → cleared
    expect(verify).not.toHaveBeenCalled()
    expect(controlPlaneOwnerId()).toBe(DEFAULT_LOCAL_OWNER_ID)
  })

})
