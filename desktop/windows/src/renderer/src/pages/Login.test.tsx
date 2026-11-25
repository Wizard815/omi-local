// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, cleanup, fireEvent, screen, waitFor } from '@testing-library/react'

const h = vi.hoisted(() => ({ signIn: vi.fn() }))

vi.mock('../lib/session', () => ({
  signIn: h.signIn,
  serverUrl: () => 'http://localhost:8000',
  serverUrlPinned: () => false
}))

import { Login } from './Login'

beforeEach(() => {
  h.signIn.mockReset()
})

afterEach(() => {
  cleanup()
})

function fill(server: string, user: string, pass: string): void {
  fireEvent.change(screen.getByLabelText('Server address'), { target: { value: server } })
  fireEvent.change(screen.getByLabelText('Username'), { target: { value: user } })
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: pass } })
}

describe('Login page (omi-local)', () => {
  it('starts on the remembered server and offers no cloud providers', () => {
    render(<Login />)
    expect((screen.getByLabelText('Server address') as HTMLInputElement).value).toBe(
      'http://localhost:8000'
    )
    expect(screen.queryByText(/Google|Apple/)).toBeNull()
  })

  it('signs in on the given server with the username and password', async () => {
    h.signIn.mockResolvedValue({ ok: true, state: {} })
    render(<Login />)
    fill('192.168.1.10:8000', 'dana', 'secret')
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    await waitFor(() => expect(h.signIn).toHaveBeenCalledWith('192.168.1.10:8000', 'dana', 'secret'))
  })

  it("shows the server's refusal", async () => {
    h.signIn.mockResolvedValue({ ok: false, error: 'Wrong username or password' })
    render(<Login />)
    fill('http://omi.lan:8000', 'dana', 'nope')
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect((await screen.findByRole('alert')).textContent).toBe('Wrong username or password')
  })
})
