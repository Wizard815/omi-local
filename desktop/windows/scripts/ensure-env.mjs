// Build prestep: guarantee a .env exists before electron-vite bakes renderer env vars.
// omi-local bakes no service config (the server is chosen at runtime), so .env.example
// holds only optional settings and copying it is always safe. CI does the same copy (desktop-windows-ci.yml); this
// makes local `build:win` / `build:unpack` match. No-op when .env already exists —
// never overwrites a real .env.
import { existsSync, copyFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const env = join(root, '.env')
const example = join(root, '.env.example')

if (existsSync(env)) {
  console.log('[ensure-env] .env present — leaving it untouched.')
  process.exit(0)
}
if (!existsSync(example)) {
  console.error('[ensure-env] FATAL: no .env and no .env.example — the build would bake undefined renderer config.')
  process.exit(1)
}
copyFileSync(example, env)
console.log('[ensure-env] no .env found — copied .env.example → .env (public production defaults).')
