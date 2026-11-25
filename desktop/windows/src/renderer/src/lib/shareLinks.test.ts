import { describe, expect, it, vi } from 'vitest'

vi.mock('./session', () => ({ serverUrl: () => 'http://omi.lan:8000' }))
import { conversationShareUrl, shareBaseUrl } from './shareLinks'

describe('shareLinks (#4339)', () => {
  it("defaults to the user's omi-local server", () => {
    expect(shareBaseUrl('')).toBe('http://omi.lan:8000')
    expect(shareBaseUrl(undefined)).toBe('http://omi.lan:8000')
    expect(conversationShareUrl('abc', '', 'sid12345')).toBe(
      'http://omi.lan:8000/conversations/abc?sid=sid12345&s=win'
    )
  })

  it('honors VITE_OMI_SHARE_BASE_URL overrides', () => {
    expect(shareBaseUrl('https://share.example.com/')).toBe('https://share.example.com')
    expect(shareBaseUrl('share.example.com')).toBe('https://share.example.com')
    expect(conversationShareUrl('abc', 'https://share.example.com', 'sid12345')).toBe(
      'https://share.example.com/conversations/abc?sid=sid12345&s=win'
    )
  })

  it('falls back for malformed or unsupported overrides', () => {
    expect(shareBaseUrl('ftp://share.example.com')).toBe('http://omi.lan:8000')
    expect(shareBaseUrl('not a url')).toBe('http://omi.lan:8000')
  })

  it('mints a random share id per link', () => {
    const first = new URL(conversationShareUrl('abc')).searchParams.get('sid')
    const second = new URL(conversationShareUrl('abc')).searchParams.get('sid')
    expect(first).toMatch(/^[0-9a-f]{32}$/)
    expect(first).not.toBe(second)
  })
})
