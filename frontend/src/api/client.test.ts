import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, getWorkspace } from './client'
import { clearSession, commitToken, getToken, onSessionUnauthorized } from './session'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn())
})

afterEach(() => {
  clearSession()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('request()', () => {
  it('attaches the session token as a bearer header when one is set', async () => {
    commitToken('my-token')
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(200, { id: 'ws-1' }))

    await getWorkspace()

    const [, init] = vi.mocked(fetch).mock.calls[0]
    const headers = new Headers(init?.headers)
    expect(headers.get('Authorization')).toBe('Bearer my-token')
  })

  it('sends no Authorization header when the session has no token', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(200, { id: 'ws-1' }))

    await getWorkspace()

    const [, init] = vi.mocked(fetch).mock.calls[0]
    const headers = new Headers(init?.headers)
    expect(headers.has('Authorization')).toBe(false)
  })

  it('clears the session and notifies listeners on a 401, distinct from other failures', async () => {
    commitToken('stale-token')
    const listener = vi.fn()
    onSessionUnauthorized(listener)
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(401, { detail: 'Invalid bearer token' }))

    await expect(getWorkspace()).rejects.toBeInstanceOf(ApiError)

    expect(getToken()).toBeNull()
    expect(listener).toHaveBeenCalledTimes(1)
  })

  it('leaves the session untouched on a non-401 error', async () => {
    commitToken('good-token')
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(500, { detail: 'boom' }))

    await expect(getWorkspace()).rejects.toBeInstanceOf(ApiError)

    expect(getToken()).toBe('good-token')
  })
})
