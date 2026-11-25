import { beforeEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('./apiClient', () => ({ omiApi: { get: h.get, post: h.post } }))

import {
  approveBatch,
  approveDay,
  approveTask,
  describeBatch,
  fetchDay,
  undoBatch,
  waitingCount,
  type Waiting
} from './approvals'

beforeEach(() => {
  h.get.mockReset()
  h.post.mockReset()
})

const waiting = (over: Partial<Waiting> = {}): Waiting => ({
  tasks: [],
  task_count: 0,
  event_count: 0,
  events: [],
  draft_dates: [],
  today: '2026-11-25',
  auto_approve: false,
  auto_approve_time: '21:00',
  agent_jobs: [],
  ...over
})

describe('approvals client (omi-local "Waiting for your OK")', () => {
  it('counts tasks, events and draft days', () => {
    expect(waitingCount(null)).toBe(0)
    expect(
      waitingCount(waiting({ task_count: 3, event_count: 2, draft_dates: ['2026-11-24'] }))
    ).toBe(6)
  })

  it('approves everything waiting in one batch and undoes exactly that batch', async () => {
    const result = {
      task_ids: ['t1'],
      events: [{ conversation_id: 'c1', event_idx: 0 }],
      day_dates: ['2026-11-24'],
      task_count: 1,
      event_count: 1,
      day_count: 1,
      failed_event_count: 0,
      failed_day_dates: []
    }
    h.post.mockResolvedValueOnce({ data: result }).mockResolvedValueOnce({ data: {} })
    const r = await approveBatch({ all_waiting: true })
    expect(h.post).toHaveBeenNthCalledWith(
      1,
      '/v1/approvals/batch',
      { all_waiting: true },
      expect.objectContaining({ timeout: 1_200_000 })
    )
    await undoBatch(r)
    expect(h.post).toHaveBeenNthCalledWith(2, '/v1/approvals/batch/undo', {
      task_ids: ['t1'],
      events: [{ conversation_id: 'c1', event_idx: 0 }],
      day_dates: ['2026-11-24']
    })
    expect(describeBatch(r)).toBe('Approved 1 task, 1 event and 1 day summary')
  })

  it('surfaces a refused task approval', async () => {
    h.post.mockResolvedValue({ data: { ok: false, error: 'Task not waiting' } })
    await expect(approveTask('x')).rejects.toThrow('Task not waiting')
    expect(h.post).toHaveBeenCalledWith('/v1/approvals/tasks/x/approve', {})
  })

  it('reads a day with its draft and approves it', async () => {
    h.get.mockResolvedValue({
      data: { date: '2026-11-24', review: null, conversations: [], processing_count: 0 }
    })
    await fetchDay('2026-11-24')
    expect(h.get).toHaveBeenCalledWith('/v1/users/day-reviews/2026-11-24', {
      params: { include_drafts: true }
    })
    h.post.mockResolvedValue({ data: { date: '2026-11-24', draft: false } })
    await approveDay('2026-11-24')
    expect(h.post).toHaveBeenCalledWith(
      '/v1/users/day-reviews/2026-11-24/approve',
      null,
      expect.objectContaining({ timeout: 1_200_000 })
    )
  })

  it('says when nothing was waiting', () => {
    expect(describeBatch({ task_count: 0, event_count: 0, day_count: 0 })).toBe(
      'Nothing was waiting'
    )
    expect(describeBatch({ task_count: 2, event_count: 0, day_count: 0 })).toBe('Approved 2 tasks')
  })
})
