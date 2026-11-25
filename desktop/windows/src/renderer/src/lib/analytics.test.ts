import { afterEach, describe, expect, it, vi } from 'vitest'

import { trackEvent, trackPaywallOpened, trackUpgradePlanSelected } from './analytics'

describe('local event log (omi-local reports nothing off the machine)', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('never makes a network call', () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response())
    vi.spyOn(console, 'info').mockImplementation(() => {})
    trackEvent('App Launched', { feature: 'voice' })
    trackPaywallOpened('windows_settings_view', 'free')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('writes one structured console line per event', () => {
    const info = vi.spyOn(console, 'info').mockImplementation(() => {})
    trackEvent('fallback_triggered', { component: 'ptt_cascade', outcome: 'degraded' })
    expect(info).toHaveBeenCalledExactlyOnceWith('[event]', 'fallback_triggered', {
      component: 'ptt_cascade',
      outcome: 'degraded'
    })
  })

  it('keeps the closed funnel vocabulary for plan and interval aliases', () => {
    const info = vi.spyOn(console, 'info').mockImplementation(() => {})
    trackPaywallOpened('windows_settings_view', 'free')
    trackUpgradePlanSelected('unlimited', 'Annual', 'not-a-source')
    expect(info.mock.calls.map((c) => [c[1], c[2]])).toEqual([
      ['Paywall Opened', { source: 'windows_settings_view', current_plan: 'basic' }],
      [
        'Upgrade Plan Selected',
        { plan: 'unlimited', billing_interval: 'year', source: 'unknown' }
      ]
    ])
  })
})
