import { describe, it, expect } from 'vitest'
import { decodeJwtClaims, decodeUidFromIdToken } from './omiAuth'

function fakeJwt(payload: Record<string, unknown>): string {
  const b64url = (o: unknown): string =>
    Buffer.from(JSON.stringify(o))
      .toString('base64')
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=+$/, '')
  return `${b64url({ alg: 'HS256' })}.${b64url(payload)}.sig`
}

describe('decodeJwtClaims', () => {
  it('decodes the payload without verification', () => {
    expect(decodeJwtClaims(fakeJwt({ email: 'a@b.c', given_name: 'Ada' }))).toMatchObject({
      email: 'a@b.c',
      given_name: 'Ada'
    })
  })

  it('returns null on malformed input', () => {
    expect(decodeJwtClaims('not-a-jwt')).toBeNull()
    expect(decodeJwtClaims('a.!!!.c')).toBeNull()
  })
})

describe('decodeUidFromIdToken', () => {
  it('reads the uid from the user_id claim', () => {
    expect(decodeUidFromIdToken(fakeJwt({ user_id: 'uid-A' }))).toBe('uid-A')
  })

  it('falls back to the sub claim when user_id is absent', () => {
    expect(decodeUidFromIdToken(fakeJwt({ sub: 'uid-B' }))).toBe('uid-B')
  })

  it('reads the uid claim of an omi-local session token', () => {
    expect(decodeUidFromIdToken(fakeJwt({ uid: 'uid-L', exp: 1 }))).toBe('uid-L')
  })

  it('prefers user_id over sub (Firebase ID tokens carry both)', () => {
    expect(decodeUidFromIdToken(fakeJwt({ user_id: 'uid-A', sub: 'uid-B' }))).toBe('uid-A')
  })

  it('returns "" for an undecodable / forged token — never guesses an identity', () => {
    expect(decodeUidFromIdToken('not-a-jwt')).toBe('')
    expect(decodeUidFromIdToken('a.!!!.c')).toBe('')
    expect(decodeUidFromIdToken('')).toBe('')
    // A well-formed JWT with no uid claim is still "no owner", not a partial one.
    expect(decodeUidFromIdToken(fakeJwt({ email: 'a@b.c' }))).toBe('')
    // A non-string claim must not be coerced into an owner id.
    expect(decodeUidFromIdToken(fakeJwt({ user_id: 42 }))).toBe('')
  })
})
