import { useCallback, useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'
import {
  CalendarDays,
  Check,
  ChevronDown,
  ChevronRight,
  ListChecks,
  RefreshCw,
  X
} from 'lucide-react'
import { PageHeader } from '../components/layout/PageHeader'
import { Markdown } from '../components/Markdown'
import { toast } from '../lib/toast'
import {
  approveBatch,
  approveDay,
  approveTask,
  declineTask,
  describeBatch,
  fetchDay,
  fetchDayReviews,
  fetchWaiting,
  undoBatch,
  waitingCount,
  type ApproveBatchResult,
  type DayReview,
  type Waiting
} from '../lib/approvals'

export const APPROVALS_PATH = '/approvals'

function formatDay(date: string): string {
  const d = new Date(`${date}T12:00:00`)
  return Number.isNaN(d.getTime())
    ? date
    : d.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })
}

function formatWhen(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? ''
    : d.toLocaleString(undefined, {
        weekday: 'short',
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit'
      })
}

/** One day's summary: read-only text by section, and Approve for a draft. */
function DayCard({
  date,
  initial,
  onApproved
}: {
  date: string
  initial?: DayReview
  onApproved: () => void
}): React.JSX.Element {
  const [open, setOpen] = useState(!!initial?.draft)
  const [review, setReview] = useState<DayReview | null>(initial ?? null)
  const [loading, setLoading] = useState(false)
  const [approving, setApproving] = useState(false)

  const loadDay = useCallback(async (): Promise<DayReview | null> => {
    setLoading(true)
    try {
      return (await fetchDay(date)).review
    } catch {
      toast('Could not load this day', { tone: 'error' })
      return null
    } finally {
      setLoading(false)
    }
  }, [date])

  useEffect(() => {
    if (!open || review) return
    let live = true
    void (async () => {
      const r = await loadDay()
      if (live) setReview(r)
    })()
    return () => {
      live = false
    }
  }, [open, review, loadDay])

  const approve = async (): Promise<void> => {
    setApproving(true)
    try {
      setReview(await approveDay(date))
      toast('Day approved', { body: 'Its summary goes to your calendar.', tone: 'success' })
      onApproved()
    } catch {
      toast('Could not approve this day', { body: 'It still waits; try again.', tone: 'error' })
    } finally {
      setApproving(false)
    }
  }

  const draft = review ? review.draft : true
  return (
    <li className="surface-card p-4" data-day={date}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-start gap-3 text-left"
        aria-expanded={open}
      >
        {open ? (
          <ChevronDown className="mt-0.5 h-4 w-4 shrink-0 text-white/40" />
        ) : (
          <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-white/40" />
        )}
        <div className="min-w-0 flex-1">
          <div className="text-[11px] font-medium uppercase tracking-wide text-white/45">
            {draft ? 'Day summary · draft · not sent anywhere' : 'Day summary · approved'}
          </div>
          <div className="mt-0.5 text-sm font-medium text-white/90">
            {formatDay(date)}
            {review?.title ? ` — ${review.title}` : ''}
          </div>
          {review && (
            <div className="mt-0.5 text-xs text-white/45">
              {review.sections.length} section{review.sections.length === 1 ? '' : 's'} from{' '}
              {review.rolled_up_conversation_ids.length} conversation
              {review.rolled_up_conversation_ids.length === 1 ? '' : 's'}
            </div>
          )}
        </div>
      </button>
      {open && (
        <div className="mt-3 pl-7">
          {loading && <p className="text-xs text-white/45">Loading…</p>}
          {review && review.sections.length > 0 && (
            <div className="space-y-3">
              {review.sections.map((s) => (
                <div key={s.id}>
                  <div className="text-xs font-semibold text-white/75">{s.title}</div>
                  <div className="text-sm leading-relaxed text-white/70">
                    <Markdown text={s.text} />
                  </div>
                </div>
              ))}
            </div>
          )}
          {review && review.sections.length === 0 && review.final_text && (
            <div className="text-sm leading-relaxed text-white/70">
              <Markdown text={review.final_text} />
            </div>
          )}
          {draft ? (
            <div className="mt-4 flex items-center gap-3">
              <button
                onClick={() => void approve()}
                disabled={approving}
                className="btn-primary px-3 py-2 disabled:opacity-50"
              >
                <Check className="h-4 w-4" />
                {approving ? 'Approving… (your model may take a while)' : 'Approve the day'}
              </button>
              <span className="text-xs text-white/40">
                Approving puts this summary on your calendar as an all-day note.
              </span>
            </div>
          ) : (
            review?.approved_at && (
              <p className="mt-3 text-xs text-white/45">
                Approved {formatWhen(review.approved_at)}.
              </p>
            )
          )}
        </div>
      )}
    </li>
  )
}

/**
 * "Waiting for your OK": everything the user's server holds back until they
 * approve it — tasks, heard calendar events and day summary drafts — with
 * Approve all (and Undo), per-item approve/decline, and the recent day reviews
 * (read-only). A deliberately plain page; the desktop UI gets its redesign later.
 */
export function Approvals(): React.JSX.Element {
  const { pathname } = useLocation()
  const active = pathname === APPROVALS_PATH
  const [waiting, setWaiting] = useState<Waiting | null>(null)
  const [days, setDays] = useState<DayReview[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState<Set<string>>(new Set())
  const [batching, setBatching] = useState(false)
  const [lastBatch, setLastBatch] = useState<ApproveBatchResult | null>(null)

  const load = useCallback(async (): Promise<void> => {
    setLoading(true)
    try {
      const [w, d] = await Promise.all([fetchWaiting(), fetchDayReviews(14).catch(() => [])])
      setWaiting(w)
      setDays(d)
      setError(null)
    } catch (e) {
      setError((e as Error).message || 'Could not reach your server')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!active) return
    void (async () => {
      await load()
    })()
  }, [active, load])

  const mark = (id: string, on: boolean): void =>
    setBusy((prev) => {
      const next = new Set(prev)
      if (on) next.add(id)
      else next.delete(id)
      return next
    })

  const onTask = async (id: string, approve: boolean): Promise<void> => {
    mark(id, true)
    try {
      if (approve) await approveTask(id)
      else await declineTask(id)
      setWaiting((w) =>
        w ? { ...w, tasks: w.tasks.filter((t) => t.id !== id), task_count: w.task_count - 1 } : w
      )
      void window.omi.tasksReconcile?.().catch(() => {})
    } catch (e) {
      toast((e as Error).message, { tone: 'error' })
    } finally {
      mark(id, false)
    }
  }

  const onEvent = async (conversationId: string, eventIdx: number): Promise<void> => {
    const key = `${conversationId}:${eventIdx}`
    mark(key, true)
    try {
      const r = await approveBatch({
        events: [{ conversation_id: conversationId, event_idx: eventIdx }]
      })
      if (r.failed_event_count)
        toast('Your calendar refused this event; it still waits', { tone: 'warn' })
      await load()
    } catch {
      toast('Could not add this event', { tone: 'error' })
    } finally {
      mark(key, false)
    }
  }

  const approveAll = async (): Promise<void> => {
    setBatching(true)
    try {
      const r = await approveBatch({ all_waiting: true })
      setLastBatch(r)
      if (r.failed_day_dates.length || r.failed_event_count) {
        toast('Some items still wait', {
          body: 'A day or an event could not be approved now; it stays here.',
          tone: 'warn'
        })
      }
      void window.omi.tasksReconcile?.().catch(() => {})
      await load()
    } catch {
      toast('Approve all failed; nothing was changed', { tone: 'error' })
    } finally {
      setBatching(false)
    }
  }

  const undo = async (): Promise<void> => {
    if (!lastBatch) return
    const batch = lastBatch
    setLastBatch(null)
    try {
      await undoBatch(batch)
      void window.omi.tasksReconcile?.().catch(() => {})
      await load()
    } catch {
      toast('Undo failed', { tone: 'error' })
    }
  }

  const count = waitingCount(waiting)
  const draftDates = waiting?.draft_dates ?? []
  const approvedDays = days.filter((d) => !d.draft && !draftDates.includes(d.date))

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Waiting for your OK"
        subtitle={
          waiting
            ? `Things Omi heard · nothing is sent until you approve · auto-approve ${
                waiting.auto_approve ? `on at ${waiting.auto_approve_time}` : 'off'
              }`
            : loading
              ? 'Loading…'
              : 'Things Omi heard · nothing is sent until you approve'
        }
        actions={
          <div className="flex items-center gap-2">
            <button
              onClick={() => void approveAll()}
              disabled={batching || count === 0}
              className="btn-primary px-3 py-2 disabled:opacity-50"
            >
              <Check className="h-4 w-4" />
              {batching ? 'Approving…' : `Approve all (${count})`}
            </button>
            <button
              onClick={() => void load()}
              disabled={loading}
              className="btn-ghost px-3 py-2 disabled:opacity-50"
              title="Refresh"
              aria-label="Refresh"
            >
              <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </div>
        }
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6 lg:px-10 lg:py-8">
        <div className="mx-auto max-w-3xl space-y-8">
          {error && <div className="surface-card p-4 text-sm text-rose-300/90">{error}</div>}
          {lastBatch && (
            <div className="surface-card flex items-center justify-between p-4 text-sm text-white/80">
              <span>{describeBatch(lastBatch)}.</span>
              <button onClick={() => void undo()} className="btn-ghost px-3 py-1.5">
                Undo
              </button>
            </div>
          )}
          {waiting && count === 0 && !lastBatch && (
            <div className="surface-card p-6 text-center text-sm text-white/55">
              Nothing is waiting. New tasks, events and day summaries show up here before they go
              anywhere.
            </div>
          )}

          {draftDates.length > 0 && (
            <section>
              <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-white/50">
                Day summaries
              </h2>
              <ul className="space-y-3">
                {draftDates.map((date) => (
                  <DayCard
                    key={date}
                    date={date}
                    initial={days.find((d) => d.date === date)}
                    onApproved={() => void load()}
                  />
                ))}
              </ul>
            </section>
          )}

          {waiting && waiting.tasks.length > 0 && (
            <section>
              <h2 className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-white/50">
                <ListChecks className="h-3.5 w-3.5" /> Tasks ({waiting.task_count})
              </h2>
              <ul className="space-y-2">
                {waiting.tasks.map((t) => (
                  <li
                    key={t.id}
                    className="surface-card flex items-center gap-3 p-3"
                    data-task={t.id}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="text-sm text-white/90">{t.description}</div>
                      <div className="mt-0.5 text-xs text-white/40">
                        {t.due_at ? `Due ${formatWhen(t.due_at)} · ` : ''}heard{' '}
                        {formatWhen(t.created_at)}
                      </div>
                    </div>
                    <button
                      onClick={() => void onTask(t.id, false)}
                      disabled={busy.has(t.id)}
                      className="btn-ghost px-2.5 py-1.5 text-xs disabled:opacity-50"
                    >
                      <X className="h-3.5 w-3.5" /> Decline
                    </button>
                    <button
                      onClick={() => void onTask(t.id, true)}
                      disabled={busy.has(t.id)}
                      className="btn-primary px-2.5 py-1.5 text-xs disabled:opacity-50"
                    >
                      <Check className="h-3.5 w-3.5" /> Approve
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {waiting && waiting.events.length > 0 && (
            <section>
              <h2 className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-white/50">
                <CalendarDays className="h-3.5 w-3.5" /> Calendar events ({waiting.event_count})
              </h2>
              <ul className="space-y-2">
                {waiting.events.map((e) => {
                  const key = `${e.conversation_id}:${e.event_idx}`
                  return (
                    <li key={key} className="surface-card flex items-center gap-3 p-3">
                      <div className="min-w-0 flex-1">
                        <div className="text-sm text-white/90">{e.title}</div>
                        <div className="mt-0.5 text-xs text-white/40">
                          {formatWhen(e.start)}
                          {e.conversation_title ? ` · from “${e.conversation_title}”` : ''}
                        </div>
                      </div>
                      <button
                        onClick={() => void onEvent(e.conversation_id, e.event_idx)}
                        disabled={busy.has(key)}
                        className="btn-primary px-2.5 py-1.5 text-xs disabled:opacity-50"
                      >
                        <Check className="h-3.5 w-3.5" /> Add to calendar
                      </button>
                    </li>
                  )
                })}
              </ul>
            </section>
          )}

          {waiting && waiting.agent_jobs.length > 0 && (
            <section>
              <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-white/50">
                Agents asking ({waiting.agent_jobs.length})
              </h2>
              <ul className="space-y-2">
                {waiting.agent_jobs.map((j) => (
                  <li key={j.id} className="surface-card p-3 text-sm text-white/80">
                    <div>{j.title || j.prompt || j.id}</div>
                    {j.approval?.question && (
                      <div className="mt-1 text-xs text-white/50">{j.approval.question}</div>
                    )}
                    <div className="mt-1 text-xs text-white/35">
                      Answer this in the Omi phone app or in {j.target || 'the agent'}.
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {approvedDays.length > 0 && (
            <section>
              <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-white/50">
                Recent days
              </h2>
              <ul className="space-y-3">
                {approvedDays.map((d) => (
                  <DayCard key={d.date} date={d.date} initial={d} onApproved={() => void load()} />
                ))}
              </ul>
            </section>
          )}
        </div>
      </div>
    </div>
  )
}
