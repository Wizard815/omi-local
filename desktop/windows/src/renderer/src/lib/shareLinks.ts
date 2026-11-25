/** Public share-link base URL for self-hosting (#4339). Matches backend OMI_SHARE_BASE_URL.
 *  omi-local: unset means the user's own server, never Omi's cloud share host. */
import { serverUrl } from './session'

export function shareBaseUrl(
  raw: string | undefined = import.meta.env.VITE_OMI_SHARE_BASE_URL as string | undefined
): string {
  let value = (raw ?? '').trim()
  const fallback = serverUrl()
  if (!value) value = fallback
  if (!value.includes('://')) value = `https://${value}`
  try {
    const parsed = new URL(value)
    if (!['http:', 'https:'].includes(parsed.protocol) || !parsed.hostname)
      return fallback
  } catch {
    return fallback
  }
  return value.replace(/\/+$/, '')
}

export function conversationShareUrl(
  id: string,
  raw?: string | undefined,
  sid: string = newShareId()
): string {
  const url = new URL(`${shareBaseUrl(raw)}/conversations/${id}`)
  url.searchParams.set('sid', sid)
  // The same Electron code also ships on Linux; avoid claiming those shares came from Windows.
  if (typeof navigator === 'undefined' || !/linux/i.test(navigator.userAgent)) {
    url.searchParams.set('s', 'win')
  }
  return url.toString()
}

/** Ephemeral link ID, independent of the sender and conversation. */
export function newShareId(): string {
  return crypto.randomUUID().replaceAll('-', '')
}
