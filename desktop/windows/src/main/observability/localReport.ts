// Error and event reports that stay on this machine.
//
// omi-local reports nothing to a third party: there is no Sentry (or any other
// crash/analytics service) in this app. What upstream sent to Sentry is written
// to the main-process log instead (userData/logs, see mainLog.ts), where the user
// can read it. Callers pass already-sanitized context; never log tokens here.

type Level = 'fatal' | 'error' | 'warning' | 'log' | 'info' | 'debug'

function describe(err: unknown): string {
  if (err instanceof Error) return `${err.name}: ${err.message}`
  return String(err)
}

/** A non-fatal error the app recovered from (e.g. database repair). */
export function captureError(
  err: unknown,
  context: { area: string; extra?: Record<string, unknown> }
): void {
  console.error(`[report] ${context.area}: ${describe(err)}`, context.extra ?? '')
}

/** A developer-facing event (a crash on the previous launch, a hung renderer). */
export function captureMessage(
  message: string,
  context: { area: string; level?: Level; extra?: Record<string, unknown> }
): void {
  const line = `[report] ${context.area}: ${message}`
  if (context.level === 'error' || context.level === 'fatal') console.error(line, context.extra ?? '')
  else console.warn(line, context.extra ?? '')
}
