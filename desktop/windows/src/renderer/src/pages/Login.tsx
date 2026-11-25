import { useRef, useState } from 'react'
import { serverUrl, serverUrlPinned, signIn } from '../lib/session'
import omiLogo from '../assets/omilogo.png'
import { BrandImage } from '../components/ui/BrandImage'

/**
 * Sign in to the user's own omi-local server: its address plus the username and
 * password seeded on it (backend/scripts/seed_local_account.py). Main posts the
 * credentials to /v1/auth/local-login and keeps the session it gets back; a
 * successful sign-in flips the auth state, which unmounts this page.
 */
export function Login(): React.JSX.Element {
  const [server, setServer] = useState(serverUrl())
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const attemptRef = useRef(0)
  const pinned = serverUrlPinned()

  const onSubmit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault()
    const attempt = ++attemptRef.current
    setError(null)
    setBusy(true)
    try {
      const result = await signIn(server, username, password)
      if (attempt !== attemptRef.current) return
      if (!result.ok) setError(result.error)
    } catch (err) {
      if (attempt !== attemptRef.current) return
      setError((err as Error).message || 'Sign-in failed')
    } finally {
      if (attempt === attemptRef.current) setBusy(false)
    }
  }

  const field =
    'w-full rounded-xl border border-white/10 bg-white/[0.04] px-4 py-3 text-sm text-white placeholder:text-white/30 outline-none focus:border-white/30'

  return (
    <div className="app-canvas relative flex h-full items-center justify-center p-8">
      <form
        onSubmit={(e) => void onSubmit(e)}
        className="animate-fade-in relative z-10 flex w-full max-w-[380px] flex-col items-center"
        aria-label="Sign in to your omi-local server"
      >
        <BrandImage src={omiLogo} alt="omi" className="h-20 w-auto" />
        <p className="mt-5 text-base leading-relaxed text-white/70">
          Sign in to your own Omi server
        </p>
        <div className="mt-10 flex w-full flex-col gap-3">
          <label className="flex flex-col gap-1.5 text-xs text-white/50">
            Server address
            <input
              className={field}
              value={server}
              onChange={(e) => setServer(e.target.value)}
              placeholder="http://192.168.1.10:8000"
              autoComplete="url"
              spellCheck={false}
              disabled={pinned}
              required
            />
          </label>
          <label className="flex flex-col gap-1.5 text-xs text-white/50">
            Username
            <input
              className={field}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              spellCheck={false}
              required
            />
          </label>
          <label className="flex flex-col gap-1.5 text-xs text-white/50">
            Password
            <input
              className={field}
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </label>
        </div>
        <button
          type="submit"
          disabled={busy}
          className="mt-6 w-full rounded-xl bg-white px-8 py-3 font-medium text-black transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
        <div className="mt-4 min-h-[3rem] text-center">
          {error && (
            <p role="alert" className="text-sm text-red-400/90">
              {error}
            </p>
          )}
          {!error && (
            <p className="text-xs leading-relaxed text-white/35">
              The account is the one your server&apos;s owner made for you. Nothing is sent
              anywhere but this server.
            </p>
          )}
        </div>
      </form>
    </div>
  )
}
