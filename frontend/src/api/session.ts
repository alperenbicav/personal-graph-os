// Runtime-authenticated browser session (ST-08.1): the bearer token is never compiled
// into the build. It is entered once through the unlock screen, then kept only in memory
// plus tab-scoped `sessionStorage` (which a new tab never inherits), and cleared on any
// later `401` so a revoked/rotated token cannot silently keep looking unlocked.

export const SESSION_STORAGE_KEY = 'pgos.apiToken'

let currentToken: string | null = null
let unauthorizedListener: (() => void) | null = null

function readSessionStorage(): string | null {
  try {
    return window.sessionStorage.getItem(SESSION_STORAGE_KEY)
  } catch {
    // sessionStorage can throw in a locked-down/private context; the token then only
    // survives in memory for the rest of this tab's lifetime.
    return null
  }
}

function writeSessionStorage(token: string): void {
  try {
    window.sessionStorage.setItem(SESSION_STORAGE_KEY, token)
  } catch {
    // Best-effort; in-memory `currentToken` still lets this tab keep working.
  }
}

function clearSessionStorage(): void {
  try {
    window.sessionStorage.removeItem(SESSION_STORAGE_KEY)
  } catch {
    // Nothing to clean up if storage was never writable.
  }
}

/** Restores a token this exact tab unlocked earlier (e.g. after a reload). Never reads a
 * token set by another tab: `sessionStorage` is tab-scoped by design. */
export function restoreSession(): string | null {
  currentToken = readSessionStorage()
  return currentToken
}

export function getToken(): string | null {
  return currentToken
}

export function setToken(token: string): void {
  currentToken = token
  writeSessionStorage(token)
}

export function clearSession(): void {
  currentToken = null
  clearSessionStorage()
}

/** Called once by the app shell to learn when a `401` invalidated the current session. */
export function onSessionUnauthorized(listener: () => void): void {
  unauthorizedListener = listener
}

/** Called by the API client on every `401`; clears the session and notifies the listener
 * so the UI can fall back to the unlock screen instead of continuing to show stale data. */
export function reportUnauthorized(): void {
  clearSession()
  unauthorizedListener?.()
}
