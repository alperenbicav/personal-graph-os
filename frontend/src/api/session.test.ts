import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  clearSession,
  getToken,
  onSessionUnauthorized,
  reportUnauthorized,
  restoreSession,
  SESSION_STORAGE_KEY,
  setToken,
} from './session'

afterEach(() => {
  clearSession()
  window.sessionStorage.clear()
})

describe('session', () => {
  it('has no token until one is set', () => {
    expect(getToken()).toBeNull()
  })

  it('setToken keeps the token in memory and in tab-scoped sessionStorage', () => {
    setToken('secret-token')

    expect(getToken()).toBe('secret-token')
    expect(window.sessionStorage.getItem(SESSION_STORAGE_KEY)).toBe('secret-token')
  })

  it('restoreSession reads a token this tab persisted earlier (e.g. after a reload)', () => {
    window.sessionStorage.setItem(SESSION_STORAGE_KEY, 'persisted-token')

    const restored = restoreSession()

    expect(restored).toBe('persisted-token')
    expect(getToken()).toBe('persisted-token')
  })

  it('restoreSession returns null when no token was ever persisted', () => {
    expect(restoreSession()).toBeNull()
  })

  it('clearSession removes the token from memory and storage', () => {
    setToken('secret-token')

    clearSession()

    expect(getToken()).toBeNull()
    expect(window.sessionStorage.getItem(SESSION_STORAGE_KEY)).toBeNull()
  })

  it('reportUnauthorized clears the session and notifies the registered listener', () => {
    setToken('secret-token')
    const listener = vi.fn()
    onSessionUnauthorized(listener)

    reportUnauthorized()

    expect(getToken()).toBeNull()
    expect(listener).toHaveBeenCalledTimes(1)
  })
})
