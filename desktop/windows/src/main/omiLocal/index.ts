// Electron wiring for the omi-local server session: the singleton, its IPC, and
// the renderer Content-Security-Policy that lets pages talk to that server only.
import { app, BrowserWindow, ipcMain, safeStorage, session as electronSession } from 'electron'
import { ServerSession } from './serverSession'
import { buildRendererCsp } from './csp'
import type { OmiLocalState } from '../../shared/omiLocal'

let instance: ServerSession | null = null

/** The process-wide session with the user's server (created on first use). */
export function serverSession(): ServerSession {
  if (!instance) {
    instance = new ServerSession({
      userDataDir: app.getPath('userData'),
      crypto: safeStorage,
      envServerUrl: process.env.OMI_SERVER_URL
    })
  }
  return instance
}

/** Base URL of the user's server, for main-process API calls. */
export function serverUrl(): string {
  return serverSession().getServerUrl()
}

function broadcast(state: OmiLocalState): void {
  for (const win of BrowserWindow.getAllWindows()) {
    if (!win.isDestroyed()) win.webContents.send('omiLocal:changed', state)
  }
}

export function registerOmiLocalHandlers(): void {
  const s = serverSession()
  s.onChange(broadcast)
  // Synchronous on purpose: every window's preload reads it once at load, so the
  // renderer knows its server and session before the first request or render.
  ipcMain.on('omiLocal:getStateSync', (event) => {
    event.returnValue = s.state()
  })
  ipcMain.handle('omiLocal:getState', () => s.state())
  ipcMain.handle('omiLocal:login', (_e, url: unknown, username: unknown, password: unknown) =>
    s.login(String(url ?? ''), String(username ?? ''), String(password ?? ''))
  )
  ipcMain.handle('omiLocal:checkServer', (_e, url: unknown) => s.checkServer(String(url ?? '')))
  ipcMain.handle('omiLocal:setServerUrl', (_e, url: unknown) => s.setServerUrl(String(url ?? '')))
  ipcMain.handle('omiLocal:signOut', () => s.signOut())
}

function isServerUrl(url: string): boolean {
  const base = serverUrl()
  return url === base || url.startsWith(`${base}/`) || url.startsWith(`${base}?`)
}

/**
 * The renderer's network rules, in the two webRequest hooks (a session has one
 * listener per hook, so they are installed together here):
 *
 *  - Requests to the user's server: drop the page's Origin and answer with
 *    permissive CORS (webSecurity stays on; the server need not list the app's
 *    loopback origin), and force a 200 on preflights.
 *  - Renderer pages: replace their CSP with one built from the configured
 *    server, so a page can open connections to that server (and loopback
 *    helpers) and nowhere else. Applied to the dev server and the packaged
 *    loopback server alike.
 */
export function installNetworkHooks(isRendererUrl: (url: string) => boolean): void {
  const web = electronSession.defaultSession.webRequest
  web.onBeforeSendHeaders((details, cb) => {
    if (!isServerUrl(details.url)) {
      cb({})
      return
    }
    const headers = { ...details.requestHeaders }
    delete headers.Origin
    delete headers.origin
    cb({ requestHeaders: headers })
  })
  web.onHeadersReceived((details, cb) => {
    if (isServerUrl(details.url)) {
      const responseHeaders = {
        ...details.responseHeaders,
        'access-control-allow-origin': ['*'],
        // `*` does not cover Authorization, so list it (and content-type) too.
        'access-control-allow-headers': ['authorization, content-type, *'],
        'access-control-allow-methods': ['GET, POST, PUT, PATCH, DELETE, OPTIONS']
      }
      if (details.method === 'OPTIONS') {
        cb({ responseHeaders, statusLine: 'HTTP/1.1 200 OK' })
        return
      }
      cb({ responseHeaders })
      return
    }
    const isPage = details.resourceType === 'mainFrame' || details.resourceType === 'subFrame'
    if (!isPage || !isRendererUrl(details.url)) {
      cb({})
      return
    }
    const headers = { ...(details.responseHeaders ?? {}) }
    for (const key of Object.keys(headers)) {
      if (key.toLowerCase() === 'content-security-policy') delete headers[key]
    }
    headers['Content-Security-Policy'] = [buildRendererCsp(serverUrl())]
    cb({ responseHeaders: headers })
  })
}
