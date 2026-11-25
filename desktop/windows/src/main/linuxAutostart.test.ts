import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import {
  AUTOSTART_FILE,
  autostartEntry,
  autostartExecutable,
  isLinuxAutostartEnabled,
  setLinuxAutostart
} from './linuxAutostart'

let dir: string
let env: NodeJS.ProcessEnv
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'omi-autostart-'))
  env = { XDG_CONFIG_HOME: dir }
})
afterEach(() => rmSync(dir, { recursive: true, force: true }))

describe('Linux XDG autostart', () => {
  it('writes and removes ~/.config/autostart/omi-local.desktop', () => {
    expect(isLinuxAutostartEnabled(env)).toBe(false)
    setLinuxAutostart(true, '/opt/Omi/omi-windows', env)
    const text = readFileSync(join(dir, 'autostart', AUTOSTART_FILE), 'utf8')
    expect(text).toContain('Exec=/opt/Omi/omi-windows --hidden')
    expect(isLinuxAutostartEnabled(env)).toBe(true)
    setLinuxAutostart(false, '/opt/Omi/omi-windows', env)
    expect(isLinuxAutostartEnabled(env)).toBe(false)
  })

  it('starts the AppImage itself and quotes paths with spaces', () => {
    expect(
      autostartExecutable({ APPIMAGE: '/home/d/Apps/Omi.AppImage' }, '/tmp/.mount_x/omi')
    ).toBe('/home/d/Apps/Omi.AppImage')
    expect(autostartEntry('/home/d/My Apps/Omi.AppImage')).toContain(
      'Exec="/home/d/My Apps/Omi.AppImage" --hidden'
    )
  })
})
