import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

function readEnvFile(name: string): Record<string, string> {
  const contents = readFileSync(resolve(__dirname, '../../', name), 'utf8')
  return Object.fromEntries(
    contents
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith('#') && line.includes('='))
      .map((line) => {
        const separator = line.indexOf('=')
        return [line.slice(0, separator), line.slice(separator + 1)]
      })
  )
}

describe('Windows release routing profiles', () => {
  // omi-local: no build profile names a service. The server is the user's runtime
  // choice (src/main/omiLocal), so neither env file may bake in a cloud host, a
  // Firebase project or an analytics/crash-report key.
  it.each(['.env.example', '.env.beta.example'])('%s bakes in no service', (name) => {
    const env = readEnvFile(name)
    const raw = readFileSync(resolve(__dirname, '../../', name), 'utf8')
    for (const key of Object.keys(env)) {
      expect(key).not.toMatch(/FIREBASE|POSTHOG|SENTRY|OMI_API_BASE|OMI_DESKTOP_API_BASE/)
    }
    expect(raw).not.toMatch(/omi\.me|omiapi\.com|run\.app|posthog|AIza/i)
  })

  it('makes the existing release workflow consume the Beta profile', () => {
    const workflow = readFileSync(
      resolve(__dirname, '../../../../.github/workflows/desktop_windows_release.yml'),
      'utf8'
    )
    expect(workflow).toContain('Copy-Item .env.beta.example .env')
    expect(workflow).not.toContain('Copy-Item .env.example .env')
  })
})
