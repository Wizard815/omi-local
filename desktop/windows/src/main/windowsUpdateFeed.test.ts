import { describe, expect, it, vi } from 'vitest'
import {
  selfHostedUpdateFeedUrl,
  WindowsUpdateFeedSelector,
  type WindowsUpdateChannel
} from './windowsUpdateFeed'

function deferred<T>(): {
  promise: Promise<T>
  resolve: (value: T) => void
} {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

describe('selfHostedUpdateFeedUrl', () => {
  it('is off unless the user names a feed', () => {
    expect(selfHostedUpdateFeedUrl(undefined)).toBeNull()
    expect(selfHostedUpdateFeedUrl('  ')).toBeNull()
  })

  it('accepts a plain http(s) directory and normalizes the trailing slash', () => {
    expect(selfHostedUpdateFeedUrl('http://nas.lan:8080/omi-desktop')).toBe(
      'http://nas.lan:8080/omi-desktop/'
    )
    expect(selfHostedUpdateFeedUrl('https://updates.example.org/omi/')).toBe(
      'https://updates.example.org/omi/'
    )
  })

  it('rejects other schemes, credentials, queries and fragments', () => {
    expect(selfHostedUpdateFeedUrl('file:///tmp/feed')).toBeNull()
    expect(selfHostedUpdateFeedUrl('https://u:p@example.org/feed')).toBeNull()
    expect(selfHostedUpdateFeedUrl('https://example.org/feed?x=1')).toBeNull()
    expect(selfHostedUpdateFeedUrl('https://example.org/feed#a')).toBeNull()
    expect(selfHostedUpdateFeedUrl('not a url')).toBeNull()
  })
})

describe('WindowsUpdateFeedSelector', () => {
  it('refreshes before later checks but does not reapply an unchanged feed', async () => {
    const resolveFeed = vi.fn(async () => {
      return 'https://github.com/BasedHardware/omi/releases/download/v1.0.1-windows/'
    })
    const applyFeed = vi.fn()
    const selector = new WindowsUpdateFeedSelector('stable', resolveFeed, applyFeed)

    await selector.prepareSelected()
    await selector.prepareSelected()

    expect(resolveFeed).toHaveBeenCalledTimes(2)
    expect(applyFeed).toHaveBeenCalledTimes(1)
  })

  it('never applies a stale channel response after a live channel switch', async () => {
    const stable = deferred<string>()
    const beta = deferred<string>()
    const resolveFeed = vi.fn((channel: WindowsUpdateChannel) => {
      return channel === 'stable' ? stable.promise : beta.promise
    })
    const applyFeed = vi.fn()
    const selector = new WindowsUpdateFeedSelector('stable', resolveFeed, applyFeed)

    const staleCheck = selector.prepareSelected()
    selector.select('beta')
    const betaCheck = selector.prepareSelected()

    beta.resolve('https://github.com/BasedHardware/omi/releases/download/v1.0.19-windows/')
    await betaCheck
    stable.resolve('https://github.com/BasedHardware/omi/releases/download/v1.0.1-windows/')
    await staleCheck

    expect(applyFeed).toHaveBeenCalledTimes(1)
    expect(applyFeed).toHaveBeenCalledWith(
      'https://github.com/BasedHardware/omi/releases/download/v1.0.19-windows/'
    )
  })
})
