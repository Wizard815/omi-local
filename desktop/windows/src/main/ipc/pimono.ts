// IPC surface for the pi-mono session relay. The renderer holds the only live
// Firebase session on Windows, so it pushes {token, desktopApiBase} here on
// sign-in and on every id-token refresh; main caches it for the pi-mono adapter
// to read at spawn (see codingAgent/piMonoSession.ts).
//
// SECURITY: the payload carries a live Firebase ID token. `configurePiMonoSession`
// validates the shape and never logs the token; this handler must never log the
// payload either.

import { ipcMain } from 'electron'
import { configurePiMonoSession, getPiMonoSession } from '../codingAgent/piMonoSession'
import {
  ensurePiMonoAdapterRegistered,
  setControlPlaneOwner
} from '../agentKernel/controlPlane'
import { serverSession } from '../omiLocal'

/** Registers the `pimono:*` IPC handlers backing the session store. */
export function registerPiMonoHandlers(): void {
  ipcMain.handle('pimono:setSession', async (_e, session: unknown): Promise<void> => {
    configurePiMonoSession(session)
    // Wire the control-plane owner to the signed-in user, HOST-DERIVED: the
    // relayed payload carries no uid, and a decode of the relayed token would trust
    // whatever a compromised renderer wrote into it. Main fetched the omi-local
    // session token itself (omiLocal/serverSession.ts), so the owner is that
    // session's uid, and only when the relayed token is exactly that token.
    // Anything else → null → the default owner, which the cold-start gate
    // (hasKnownControlPlaneOwner) refuses: fail closed. Sign-out resets it.
    const current = getPiMonoSession()
    const uid = current ? serverSession().uidForRelayedToken(current.token) : null
    setControlPlaneOwner(uid)
    // Register the managed-cloud pi-mono adapter into the kernel now that a
    // session may be present. Idempotent, and a no-op when signed out (returns
    // false), so the registry stays empty until a real Firebase session exists.
    // DARK: registration only — nothing routes chat to pi-mono until PR-E.
    ensurePiMonoAdapterRegistered()
  })

}
