import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  clearSession,
  commitToken,
  getToken,
  onSessionUnauthorized,
  reportUnauthorized,
  restoreSession,
  setPendingToken,
} from './session'

afterEach(() => {
  clearSession()
})

describe('session', () => {
  it('has no token until one is committed', () => {
    expect(getToken()).toBeNull()
  })

  it('setPendingToken holds a candidate in memory only, without persisting it', () => {
    setPendingToken('candidate-token')

    expect(getToken()).toBe('candidate-token')
    expect(restoreSession()).toBeNull() // nothing was ever committed/persisted
  })

  it('commitToken persists the token so a later restore (e.g. after a reload) finds it', () => {
    commitToken('verified-token')

    expect(getToken()).toBe('verified-token')
    expect(restoreSession()).toBe('verified-token')
  })

  it('a fresh browsing context (no history state) restores no token', () => {
    // Simulates a brand new tab/browsing context, including one opened via `window.open`
    // from an already-unlocked tab: it always starts with an empty history entry.
    window.history.replaceState(null, '')

    expect(restoreSession()).toBeNull()
  })

  it('clearSession removes the token from memory and from persisted history state', () => {
    commitToken('verified-token')

    clearSession()

    expect(getToken()).toBeNull()
    expect(restoreSession()).toBeNull()
  })

  it('reportUnauthorized clears the session and notifies the registered listener', () => {
    commitToken('verified-token')
    const listener = vi.fn()
    onSessionUnauthorized(listener)

    reportUnauthorized()

    expect(getToken()).toBeNull()
    expect(listener).toHaveBeenCalledTimes(1)
  })
})
