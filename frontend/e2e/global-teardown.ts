import { readFileSync, rmSync } from 'node:fs'
import path from 'node:path'
import { STATE_PATH } from './global-setup.js'

export default async function globalTeardown(): Promise<void> {
  let state: { pid?: number; workspaceDir?: string }
  try {
    state = JSON.parse(readFileSync(STATE_PATH, 'utf-8'))
  } catch {
    return
  }

  if (state.pid) {
    try {
      process.kill(state.pid, 'SIGTERM')
    } catch {
      // Already exited.
    }
  }
  if (state.workspaceDir) {
    rmSync(state.workspaceDir, { recursive: true, force: true })
  }
  // Remove the whole run-local state directory, not just the file (ST08-F04): it briefly
  // held the real acceptance bearer token and must not linger under the shared temp root.
  rmSync(path.dirname(STATE_PATH), { recursive: true, force: true })
}
