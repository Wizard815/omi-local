import { useState } from 'react'
import { User, LogOut, Server } from 'lucide-react'
import { auth, serverUrl, serverUrlPinned } from '../../../lib/session'
import { signOutUser } from '../../../lib/account'
import { getPreferences, setPreferences } from '../../../lib/preferences'
import { setDisplayName } from '../../../lib/userProfile'
import { toast } from '../../../lib/toast'
import { SettingRow } from '../SettingRow'

export function AccountTab(): React.JSX.Element {
  const prefs = getPreferences()
  const [name, setName] = useState(prefs.displayName ?? '')

  // Transcription language moved to Settings → Transcription (Mac parity); this
  // row now owns only the display name.
  const saveProfile = (): void => {
    setPreferences({ displayName: name.trim() })
    void setDisplayName(name.trim()).catch(() => toast('Name sync failed', { tone: 'warn' }))
    toast('Profile saved', { tone: 'success' })
  }

  const [server, setServer] = useState(serverUrl())
  const [serverNote, setServerNote] = useState<string | null>(null)
  const pinned = serverUrlPinned()

  const testServer = async (): Promise<void> => {
    setServerNote('Checking…')
    const result = await window.omi.omiLocalCheckServer(server)
    setServerNote(result.ok ? `Reachable: ${result.serverUrl}` : result.error)
  }

  // A different server is a different account: main signs out, and the app
  // returns to the sign-in screen with the new address filled in.
  const switchServer = async (): Promise<void> => {
    const result = await window.omi.omiLocalSetServerUrl(server)
    if (!result.ok) setServerNote(result.error)
  }

  return (
    <>
      <SettingRow
        icon={Server}
        title="Server"
        subtitle={
          pinned
            ? `${serverUrl()} (set by OMI_SERVER_URL)`
            : 'Your omi-local server. Changing it signs you out.'
        }
        keywords="server url address omi-local host self-hosted backend"
      >
        <div className="space-y-3">
          <input
            value={server}
            onChange={(e) => setServer(e.target.value)}
            placeholder="http://192.168.1.10:8000"
            spellCheck={false}
            disabled={pinned}
            aria-label="Server address"
            className="glass-subtle w-full rounded-lg px-4 py-3 text-sm text-text-secondary focus:outline-none"
          />
          <div className="flex gap-2">
            <button onClick={() => void testServer()} className="btn-ghost">
              Test
            </button>
            {!pinned && server.trim() !== serverUrl() && (
              <button onClick={() => void switchServer()} className="btn-ghost">
                Switch server
              </button>
            )}
          </div>
          {serverNote && <p className="text-xs text-text-tertiary">{serverNote}</p>}
        </div>
      </SettingRow>
      <SettingRow
        icon={User}
        title="Profile"
        subtitle="Your display name."
        keywords="name profile display"
      >
        <div className="space-y-3">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Your name"
            className="glass-subtle w-full rounded-lg px-4 py-3 text-sm text-text-secondary focus:outline-none"
          />
          <button onClick={saveProfile} className="btn-ghost">
            Save
          </button>
        </div>
      </SettingRow>
      <SettingRow
        icon={LogOut}
        title="Signed in"
        subtitle={
          auth.currentUser
            ? `${auth.currentUser.username || auth.currentUser.uid} on ${serverUrl()}`
            : '(not signed in)'
        }
        keywords="account email sign out logout"
        control={
          <button onClick={signOutUser} className="btn-ghost">
            Sign out
          </button>
        }
      />
    </>
  )
}
