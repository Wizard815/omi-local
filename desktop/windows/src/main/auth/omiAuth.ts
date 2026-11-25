// JWT claim helpers for the session token main holds (omi-local signs it; see
// omiLocal/serverSession.ts). Decode only, never verify: callers trust a claim
// only for a token main fetched itself. No Electron/IO imports (unit-testable).

/** Decode a JWT payload WITHOUT verification — display-only claims (name/email)
 *  from the backend-returned provider id_token. Returns null on any malformation. */
export function decodeJwtClaims(jwt: string): Record<string, unknown> | null {
  const parts = jwt.split('.')
  if (parts.length !== 3) return null
  try {
    const b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    const parsed = JSON.parse(Buffer.from(b64, 'base64').toString('utf8'))
    return typeof parsed === 'object' && parsed !== null ? parsed : null
  } catch {
    return null
  }
}

/**
 * The Firebase uid from an ID token's `user_id`/`sub` claim (decode, NOT verify).
 *
 * Host-authoritative: the uid comes from the credential ITSELF, not from a
 * separate caller-asserted field a renderer could forge. Returns '' when the
 * token is absent or undecodable — callers treat '' as "no known owner" and must
 * fail closed (never fall back to a shared default identity). Shared by every
 * main-side uid-from-token site (omiListen, pi-mono owner wiring).
 */
export function decodeUidFromIdToken(token: string): string {
  const claims = decodeJwtClaims(token)
  if (!claims) return ''
  // omi-local session tokens carry the uid as `uid` (backend/utils/local_auth.py).
  const uid = claims.user_id ?? claims.sub ?? claims.uid
  return typeof uid === 'string' ? uid : ''
}
