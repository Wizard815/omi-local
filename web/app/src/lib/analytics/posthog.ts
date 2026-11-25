import { omiFlagEnabled, resetOmiFlags } from '@/lib/omiFlags';

let flagOwnerId: string | undefined;

/**
 * omi-local: there is no third-party analytics. The upstream call sites keep
 * this interface, but nothing is sent anywhere: track/identify/pageView are
 * no-ops. Usage the server cares about is recorded server-side, on the user's
 * own server (backend/database/local_usage.py). Flags still come from the Omi
 * flag store, and a sign-out or user switch still clears them.
 */
export const PostHogManager = {
  init() {},
  identify(userId: string, _properties?: { name?: string; email?: string }) {
    if (flagOwnerId !== userId) {
      resetOmiFlags();
      flagOwnerId = userId;
    }
  },
  track(_event: string, _properties?: Record<string, unknown>) {},
  /** Omi is the only flag authority; missing or unavailable reads fail closed. */
  async isFeatureEnabled(flag: string, timeoutMs = 2000): Promise<boolean> {
    try {
      return (await omiFlagEnabled(flag, { timeoutMs })) ?? false;
    } catch {
      return false;
    }
  },
  pageView(_pageName: string) {},
  reset() {
    resetOmiFlags();
    flagOwnerId = undefined;
  },
  setUserProperty(_key: string, _value: unknown) {},
};
