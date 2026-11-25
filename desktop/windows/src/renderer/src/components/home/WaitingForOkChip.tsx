import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Inbox } from 'lucide-react'
import { fetchWaiting, waitingCount } from '../../lib/approvals'
import { APPROVALS_PATH } from '../../pages/Approvals'

/**
 * omi-local: a one-line "N waiting for your OK" chip on Home, the way into the
 * approvals page (the Hub has no nav rail). Renders nothing while nothing waits
 * or the server is unreachable. Re-reads whenever Home comes back on screen.
 */
export function WaitingForOkChip(): React.JSX.Element | null {
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const [count, setCount] = useState(0)

  useEffect(() => {
    if (pathname !== '/home') return
    let live = true
    fetchWaiting()
      .then((w) => live && setCount(waitingCount(w)))
      .catch(() => live && setCount(0))
    return () => {
      live = false
    }
  }, [pathname])

  if (count === 0) return null
  return (
    <button
      type="button"
      onClick={() => navigate(APPROVALS_PATH)}
      className="focus-ring mb-2 inline-flex items-center gap-2 rounded-full border border-amber-300/25 bg-amber-300/10 px-3 py-1 text-xs font-medium text-amber-100/90 transition-colors hover:bg-amber-300/15"
    >
      <Inbox className="h-3.5 w-3.5" aria-hidden="true" />
      {count} waiting for your OK
    </button>
  )
}
