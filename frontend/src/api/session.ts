// Runtime-authenticated browser session (ST-08.1, hardened per ST08-F01/F02 review).
//
// - Verify-then-commit: `setPendingToken` only holds a candidate in memory so a
//   verification request can use it; nothing is persisted until `commitToken` runs after
//   that request actually succeeds. A crash, reload, or network/5xx failure mid-unlock can
//   therefore never leave an unverified token retained anywhere.
// - Persistence uses this tab's `history.state` (via `replaceState`), not `sessionStorage`.
//   `sessionStorage` is cloned into a same-origin tab opened via `window.open`/"duplicate
//   tab", which would silently unlock a copy; a freshly created browsing context always
//   starts with an empty history entry (`state === null`) regardless of its opener, so only
//   an actual reload of this exact tab ever restores a committed token.

const HISTORY_STATE_KEY = 'pgosApiToken'

let currentToken: string | null = null
let unauthorizedListener: (() => void) | null = null

function readPersistedToken(): string | null {
  try {
    const state = window.history.state as Record<string, unknown> | null
    const token = state?.[HISTORY_STATE_KEY]
    return typeof token === 'string' ? token : null
  } catch {
    return null
  }
}

function writePersistedToken(token: string | null): void {
  try {
    window.history.replaceState(token ? { [HISTORY_STATE_KEY]: token } : null, '')
  } catch {
    // Same-origin `replaceState` is not expected to throw; if it ever does, the in-memory
    // `currentToken` still lets this tab keep working for the rest of its lifetime.
  }
}

/** Restores a token this exact tab already verified and committed (e.g. after a reload).
 * Never restores a token for a new browsing context, including one opened via
 * `window.open` from an already-unlocked tab. */
export function restoreSession(): string | null {
  currentToken = readPersistedToken()
  return currentToken
}

export function getToken(): string | null {
  return currentToken
}

/** Holds a candidate token in memory only, so a verification request (e.g. `getWorkspace()`)
 * can send it. Does not persist anything — call `commitToken` only after that request
 * actually succeeds, or `clearSession` if it does not. */
export function setPendingToken(token: string): void {
  currentToken = token
}

/** Persists a token this tab has already verified via a successful authenticated request. */
export function commitToken(token: string): void {
  currentToken = token
  writePersistedToken(token)
}

export function clearSession(): void {
  currentToken = null
  writePersistedToken(null)
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
