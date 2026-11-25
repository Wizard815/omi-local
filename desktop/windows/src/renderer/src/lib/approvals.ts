// "Waiting for your OK" (omi-local): what the server holds until the user
// approves it — tasks extracted from conversations, heard calendar events and
// day summary drafts — and the day review that a summary belongs to.
//
// Server contract (backend/routers/approvals.py, routers/day_reviews.py):
//   GET  /v1/approvals                              tasks + events + draft days waiting
//   POST /v1/approvals/tasks/{id}/approve | decline one task
//   POST /v1/approvals/batch {all_waiting | ids}    Approve all / selected
//   POST /v1/approvals/batch/undo {what it approved}
//   GET  /v1/users/day-reviews?include_drafts=true  recent day reviews
//   GET  /v1/users/day-reviews/{date}?include_drafts=true
//   POST /v1/users/day-reviews/{date}/approve       approve one day's draft
// Nothing here is sent anywhere but the user's own server.
import { omiApi } from './apiClient'

export type WaitingTask = {
  id: string
  description: string
  due_at?: string | null
  created_at?: string | null
  conversation_id?: string | null
  approval?: string | null
}

export type WaitingEvent = {
  conversation_id: string
  event_idx: number
  conversation_title?: string
  title: string
  description?: string
  start: string
  duration?: number
}

export type AgentJobAsking = {
  id: string
  title?: string
  prompt?: string
  target?: string
  approval?: { question?: string } | null
}

export type Waiting = {
  tasks: WaitingTask[]
  task_count: number
  event_count: number
  events: WaitingEvent[]
  draft_dates: string[]
  today: string
  auto_approve: boolean
  auto_approve_time: string
  agent_jobs: AgentJobAsking[]
}

export type ApproveBatchResult = {
  task_ids: string[]
  events: { conversation_id: string; event_idx: number }[]
  day_dates: string[]
  task_count: number
  event_count: number
  day_count: number
  failed_event_count: number
  failed_day_dates: string[]
}

export type DayReviewSection = {
  id: string
  title: string
  text: string
  tags: string[]
  conversation_ids: string[]
}

export type DayReview = {
  date: string
  draft: boolean
  title: string
  final_text: string
  sections: DayReviewSection[]
  kept_conversation_ids: string[]
  rolled_up_conversation_ids: string[]
  dropped_conversation_ids: string[]
  approved_at?: string | null
  edited_by_user_at?: string | null
}

export type DayReviewDay = {
  date: string
  review: DayReview | null
  conversations: { id: string; title: string; emoji?: string }[]
  processing_count: number
}

/** How many things wait in total (the nav badge and the Approve all label). */
export function waitingCount(w: Waiting | null): number {
  if (!w) return 0
  return w.task_count + w.event_count + w.draft_dates.length
}

export async function fetchWaiting(): Promise<Waiting> {
  const r = await omiApi.get<Waiting>('/v1/approvals')
  return r.data
}

export async function approveTask(id: string): Promise<void> {
  const r = await omiApi.post<{ ok: boolean; error?: string }>(
    `/v1/approvals/tasks/${encodeURIComponent(id)}/approve`,
    {}
  )
  if (!r.data.ok) throw new Error(r.data.error || 'Could not approve this task')
}

export async function declineTask(id: string): Promise<void> {
  const r = await omiApi.post<{ ok: boolean; error?: string }>(
    `/v1/approvals/tasks/${encodeURIComponent(id)}/decline`,
    { declined: true }
  )
  if (!r.data.ok) throw new Error(r.data.error || 'Could not decline this task')
}

/** Approve everything waiting (optionally of one local day), or the listed items.
 *  Day approval can call the model, so this gets a long timeout. */
export async function approveBatch(
  body:
    | { all_waiting: true; date?: string }
    | {
        task_ids?: string[]
        events?: { conversation_id: string; event_idx: number }[]
        day_dates?: string[]
      }
): Promise<ApproveBatchResult> {
  const r = await omiApi.post<ApproveBatchResult>('/v1/approvals/batch', body, {
    timeout: 20 * 60_000
  })
  return r.data
}

/** Take back exactly what one batch approved. */
export async function undoBatch(result: ApproveBatchResult): Promise<void> {
  await omiApi.post('/v1/approvals/batch/undo', {
    task_ids: result.task_ids,
    events: result.events,
    day_dates: result.day_dates
  })
}

export async function fetchDayReviews(limit = 14): Promise<DayReview[]> {
  const r = await omiApi.get<{ reviews: DayReview[] }>('/v1/users/day-reviews', {
    params: { limit, include_drafts: true }
  })
  return r.data.reviews ?? []
}

export async function fetchDay(date: string): Promise<DayReviewDay> {
  const r = await omiApi.get<DayReviewDay>(`/v1/users/day-reviews/${date}`, {
    params: { include_drafts: true }
  })
  return r.data
}

/** Approve a day's summary draft (it goes to the user's calendar). The server may
 *  rewrite the summary with the model first, so wait as long as chat does. */
export async function approveDay(date: string): Promise<DayReview> {
  const r = await omiApi.post<DayReview>(`/v1/users/day-reviews/${date}/approve`, null, {
    timeout: 20 * 60_000
  })
  return r.data
}

/** A short "1 task, 2 events and 1 day summary" line for a batch result. */
export function describeBatch(
  r: Pick<ApproveBatchResult, 'task_count' | 'event_count' | 'day_count'>
): string {
  const parts: string[] = []
  const n = (count: number, one: string, many: string): string =>
    `${count} ${count === 1 ? one : many}`
  if (r.task_count) parts.push(n(r.task_count, 'task', 'tasks'))
  if (r.event_count) parts.push(n(r.event_count, 'event', 'events'))
  if (r.day_count) parts.push(n(r.day_count, 'day summary', 'day summaries'))
  if (!parts.length) return 'Nothing was waiting'
  const last = parts.pop()
  return `Approved ${parts.length ? `${parts.join(', ')} and ${last}` : last}`
}
