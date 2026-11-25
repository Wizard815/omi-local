// Product events and fallback telemetry, kept on this machine.
//
// omi-local sends no analytics anywhere: upstream posted these events to Based
// Hardware's PostHog project; here each event is one structured console line
// (`[event] <name> {...}`), the renderer twin of main's observability/fallback.ts.
// The shared `fallback_triggered` shape (AGENTS.md "Fallback / resilience
// telemetry") stays greppable in the devtools console and the main log, and
// nothing is uploaded. Never put tokens or message text in properties.

export function trackEvent(event: string, properties: Record<string, unknown> = {}): void {
  console.info('[event]', event, properties)
}

export function trackHowDidYouHear(source: string): void {
  trackEvent('Onboarding How Did You Hear', { source, is_referral: source === 'Friend' })
}

const FUNNEL_PLANS = new Set([
  'basic',
  'plus',
  'unlimited',
  'unlimited_v2',
  'operator',
  'architect',
  'unknown'
])

const FUNNEL_SOURCES = new Set([
  'chat_quota_exceeded',
  'freemium_switch',
  'locked_conversation',
  'locked_memory',
  'chat_apps',
  'usage_page',
  'transcription_settings',
  'unlimited_deeplink',
  'macos_billing',
  'macos_usage_limit',
  'macos_chat_quota_banner',
  'windows_billing',
  'windows_usage_limit',
  'web_plans',
  'macos_settings_view',
  'windows_settings_view',
  'unknown'
])

function subscriptionPlanWire(raw: string | null | undefined): string {
  const value = raw?.trim() ?? ''
  if (value === 'free') return 'basic'
  if (value === 'pro') return 'unknown'
  return FUNNEL_PLANS.has(value) ? value : 'unknown'
}

function subscriptionIntervalWire(raw: string | null | undefined): string {
  const value = raw?.trim().toLowerCase() ?? ''
  // Year before month so a title that contains both words is not labeled monthly.
  if (
    value === 'year' ||
    value === 'yearly' ||
    value === 'annual' ||
    value.includes('year') ||
    value.includes('annual')
  ) {
    return 'year'
  }
  if (value === 'month' || value === 'monthly' || value.includes('month')) return 'month'
  return 'unknown'
}

function subscriptionSourceWire(raw: string | null | undefined): string {
  const value = raw?.trim() ?? ''
  return FUNNEL_SOURCES.has(value) ? value : 'unknown'
}

export function trackPaywallOpened(source: string, currentPlan: string): void {
  trackEvent('Paywall Opened', {
    source: subscriptionSourceWire(source),
    current_plan: subscriptionPlanWire(currentPlan)
  })
}

export function trackUpgradePlanSelected(
  plan: string,
  billingInterval: string,
  source: string
): void {
  trackEvent('Upgrade Plan Selected', {
    plan: subscriptionPlanWire(plan),
    billing_interval: subscriptionIntervalWire(billingInterval),
    source: subscriptionSourceWire(source)
  })
}

export function trackUpgradeSucceeded(plan: string, billingInterval: string): void {
  const properties = {
    plan: subscriptionPlanWire(plan),
    billing_interval: subscriptionIntervalWire(billingInterval)
  }
  trackEvent('Upgrade Succeeded Attributed', properties)
  trackEvent('Upgrade Succeeded')
}

export function trackUpgradeCancelled(): void {
  trackEvent('Upgrade Cancelled')
}
