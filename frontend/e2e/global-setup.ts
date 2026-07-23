import { ChildProcess, spawn } from 'node:child_process'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = path.resolve(here, '..', '..')
const FRONTEND_DIST = path.resolve(here, '..', 'dist')
const ACCEPTANCE_PORT = 8199

// A fixed path, not a random one computed at import time: `global-setup.ts` is re-evaluated
// as a fresh ES module in every worker process that imports `STATE_PATH` from a spec file,
// so a per-import `mkdtempSync()` here would give each of them a different, nonexistent
// directory. Security instead comes from permissions (ST08-F04): the directory is created
// fresh with mode 0700 and the coordination file inside with mode 0600, both owned by the
// current user, and `globalTeardown.ts` removes the whole directory when the run ends.
const STATE_DIR = path.join(tmpdir(), 'pgos-playwright-e2e-state')
export const STATE_PATH = path.join(STATE_DIR, 'state.json')

async function waitForServer(url: string, timeoutMs = 20_000): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url)
      if (response.status < 500) return
    } catch {
      // Not accepting connections yet.
    }
    await new Promise((resolve) => setTimeout(resolve, 200))
  }
  throw new Error(`Server at ${url} did not become ready within ${timeoutMs}ms`)
}

function killServer(serverProcess: ChildProcess): void {
  if (serverProcess.pid) {
    try {
      process.kill(serverProcess.pid, 'SIGTERM')
    } catch {
      // Already exited.
    }
  }
}

// Spawns the real packaged server (not a mock) against a fresh, isolated temp workspace and
// the real `frontend/dist` production build — this is the same process a real deployment
// runs, exercised end to end through a real browser in the e2e/*.spec.ts files. Called once,
// by the main test-runner process, before any worker starts.
export default async function globalSetup(): Promise<void> {
  // Start clean: a prior crashed run could have left this fixed path behind.
  rmSync(STATE_DIR, { recursive: true, force: true })
  mkdirSync(STATE_DIR, { recursive: true, mode: 0o700 })

  const workspaceDir = mkdtempSync(path.join(tmpdir(), 'pgos-e2e-workspace-'))
  const databasePath = path.join(workspaceDir, 'graph.db')

  const serverProcess = spawn('uv', ['run', 'python', '-m', 'personal_graph_os.api'], {
    cwd: REPO_ROOT,
    env: {
      ...process.env,
      PGOS_DB_PATH: databasePath,
      PGOS_PORT: String(ACCEPTANCE_PORT),
      PGOS_STATIC_DIR: FRONTEND_DIST,
      PGOS_TRUSTED_HOSTS: '127.0.0.1,localhost',
    },
    stdio: 'ignore',
  })

  try {
    await waitForServer(`http://127.0.0.1:${ACCEPTANCE_PORT}/app/`)

    const tokenPath = path.join(workspaceDir, 'api-token')
    // `create_app()` writes this file synchronously during startup; `waitForServer` above
    // already confirms the process is serving, but poll briefly in case of a rare race.
    let token = ''
    for (let attempt = 0; attempt < 25 && !token; attempt++) {
      try {
        token = readFileSync(tokenPath, 'utf-8').trim()
      } catch {
        await new Promise((resolve) => setTimeout(resolve, 100))
      }
    }
    if (!token) {
      throw new Error(`Bearer token file never appeared at ${tokenPath}`)
    }

    writeFileSync(
      STATE_PATH,
      JSON.stringify({
        port: ACCEPTANCE_PORT,
        token,
        workspaceDir,
        pid: serverProcess.pid,
      }),
      { mode: 0o600 },
    )
  } catch (error) {
    // Setup failed partway through: never leave the spawned server, its workspace, or a
    // half-written state file behind (ST08-F04 also covers the failure path, not only the
    // success path teardown already handled).
    killServer(serverProcess)
    rmSync(workspaceDir, { recursive: true, force: true })
    rmSync(STATE_DIR, { recursive: true, force: true })
    throw error
  }
}
