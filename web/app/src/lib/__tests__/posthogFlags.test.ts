import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/omiFlags', () => ({ omiFlagEnabled: vi.fn(), resetOmiFlags: vi.fn() }));
import { omiFlagEnabled, resetOmiFlags } from '@/lib/omiFlags';
import { PostHogManager } from '@/lib/analytics/posthog';

describe('omi-local: no third-party analytics, Omi-only flags', () => {
  beforeEach(() => vi.clearAllMocks());

  it('sends nothing anywhere: capture, identify and page views are no-ops', () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    PostHogManager.init();
    PostHogManager.identify('test-user', { email: 'private@example.com' });
    PostHogManager.track('Chat Message Sent', { a: 1 });
    PostHogManager.pageView('Home');
    PostHogManager.setUserProperty('k', 'v');
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it('a user switch or sign-out still clears cached flags', () => {
    PostHogManager.identify('a');
    PostHogManager.identify('a');
    PostHogManager.identify('b');
    PostHogManager.reset();
    expect(resetOmiFlags).toHaveBeenCalledTimes(3);
  });

  it('uses Omi; unknown, failed and throwing reads fail closed', async () => {
    vi.mocked(omiFlagEnabled)
      .mockResolvedValueOnce(true)
      .mockResolvedValueOnce(false)
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error('offline'));
    expect(await PostHogManager.isFeatureEnabled('on')).toBe(true);
    expect(await PostHogManager.isFeatureEnabled('off')).toBe(false);
    expect(await PostHogManager.isFeatureEnabled('missing')).toBe(false);
    expect(await PostHogManager.isFeatureEnabled('failed')).toBe(false);
  });
});
