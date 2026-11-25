// Launch-at-login on Linux: an XDG autostart entry.
//
// Electron's app.setLoginItemSettings works on Windows and macOS only; on Linux
// it does nothing. Desktop sessions that follow the XDG autostart spec (GNOME,
// KDE, Xfce, Cinnamon, MATE, LXQt; niri/Sway/Hyprland only with an autostart
// helper such as dex or systemd's xdg-autostart-generator) start every
// `~/.config/autostart/*.desktop` entry at login, so that is the switch here.
// The entry launches the real binary (an AppImage's own path when run from one)
// with --hidden: tray-only start, the same as the Windows Run key.
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

export const AUTOSTART_FILE = 'omi-local.desktop'

export function autostartDir(env: NodeJS.ProcessEnv = process.env): string {
  const base = env.XDG_CONFIG_HOME?.trim() || join(homedir(), '.config')
  return join(base, 'autostart')
}

/** Quote one Exec argument per the Desktop Entry spec. */
function execArg(arg: string): string {
  if (!/[\s"'\\$`]/.test(arg)) return arg
  return `"${arg.replace(/(["\\$`])/g, '\\$1')}"`
}

export function autostartEntry(executable: string): string {
  return [
    '[Desktop Entry]',
    'Type=Application',
    'Name=Omi',
    'Comment=Omi (omi-local), started hidden in the tray',
    `Exec=${execArg(executable)} --hidden`,
    'Terminal=false',
    'X-GNOME-Autostart-enabled=true',
    ''
  ].join('\n')
}

/** The binary to start at login: the AppImage itself, not its mounted copy. */
export function autostartExecutable(env: NodeJS.ProcessEnv, execPath: string): string {
  return env.APPIMAGE?.trim() || execPath
}

export function isLinuxAutostartEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  const file = join(autostartDir(env), AUTOSTART_FILE)
  if (!existsSync(file)) return false
  try {
    return !/^\s*Hidden\s*=\s*true\s*$/im.test(readFileSync(file, 'utf8'))
  } catch {
    return false
  }
}

export function setLinuxAutostart(
  enabled: boolean,
  executable: string,
  env: NodeJS.ProcessEnv = process.env
): void {
  const dir = autostartDir(env)
  const file = join(dir, AUTOSTART_FILE)
  if (!enabled) {
    rmSync(file, { force: true })
    return
  }
  mkdirSync(dir, { recursive: true })
  writeFileSync(file, autostartEntry(executable))
}
