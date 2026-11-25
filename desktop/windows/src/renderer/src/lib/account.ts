// The user-initiated Sign Out: the NUCLEAR path (vs the LIGHT session drop on a
// dead session's 401 — see authSession.forceReauth). Tear down all user-scoped
// local data FIRST so a second account on this machine can't see it, THEN drop
// the session main holds.
import { auth, signOut } from './session'
import { teardownUserData } from './authTeardown'

export async function signOutUser(): Promise<void> {
  // Grab the token BEFORE signing out so BYOK can be deactivated server-side
  // while the session is still valid: teardownUserData wipes the local keys, and
  // this DELETE drops the matching backend enrollment. Best-effort.
  const token = await auth.currentUser?.getIdToken().catch(() => undefined)
  await teardownUserData()
  if (token) {
    try {
      await window.omi.byokDeactivate(token)
    } catch {
      /* best-effort; the backend heartbeat TTL also lapses the activation */
    }
  }
  await signOut(auth)
}
