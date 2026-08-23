import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, deleteResource, getWorkspace, messageAgent } from './client'
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

describe('deleteResource()', () => {
  it('issues a hard DELETE with the resource id echoed as confirm_id', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(200, null))

    await deleteResource('res-123')

    const [url, init] = vi.mocked(fetch).mock.calls[0]
    expect(String(url)).toContain('/resources/res-123/hard')
    expect(String(url)).toContain('confirm_id=res-123')
    expect(init?.method).toBe('DELETE')
  })
})

describe('messageAgent()', () => {
  function sseResponse(events: string[]): Response {
    const encoder = new TextEncoder()
    const stream = new ReadableStream({
      start(controller) {
        for (const event of events) {
          controller.enqueue(encoder.encode(event))
        }
        controller.close()
      },
    })
    return new Response(stream, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    })
  }

  it('consumes SSE stream, accumulates text deltas and extracts run_id', async () => {
    const deltas: string[] = []
    const sseChunks = [
      'data: {"text": "Hello "}\n\n',
      'data: {"text": "world!"}\n\n',
      'data: {"run_id": "run-123", "done": true}\n\n',
      'data: [DONE]\n\n',
    ]

    vi.mocked(fetch).mockResolvedValueOnce(sseResponse(sseChunks))

    const result = await messageAgent('agent-1', 'Hi', undefined, (delta) => {
      deltas.push(delta)
    })

    expect(result).toEqual({
      agent_id: 'agent-1',
      run_id: 'run-123',
      reply: 'Hello world!',
      status: 'applied',
    })
    expect(deltas).toEqual(['Hello ', 'world!'])
  })

  it('falls back to JSON parsing when content-type is application/json', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse(200, {
        reply: 'Static JSON reply',
        run_id: 'run-json-456',
        status: 'applied',
      }),
    )

    const result = await messageAgent('agent-2', 'Question')

    expect(result).toEqual({
      agent_id: 'agent-2',
      run_id: 'run-json-456',
      reply: 'Static JSON reply',
      status: 'applied',
    })
  })

  it('throws ApiError when stream yields an error event', async () => {
    const sseChunks = [
      'data: {"error": "LLM context length exceeded"}\n\n',
    ]

    vi.mocked(fetch).mockResolvedValueOnce(sseResponse(sseChunks))

    await expect(messageAgent('agent-3', 'Long prompt')).rejects.toThrow(
      'LLM context length exceeded',
    )
  })
})
