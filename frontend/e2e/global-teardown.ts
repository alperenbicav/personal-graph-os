import { readFileSync, rmSync } from 'node:fs'
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
}
