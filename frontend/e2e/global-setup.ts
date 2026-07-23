import { spawn } from 'node:child_process'
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = path.resolve(here, '..', '..')
const FRONTEND_DIST = path.resolve(here, '..', 'dist')
const ACCEPTANCE_PORT = 8199

export const STATE_PATH = path.join(tmpdir(), 'pgos-e2e-state.json')

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

// Spawns the real packaged server (not a mock) against a fresh, isolated temp workspace and
// the real `frontend/dist` production build — this is the same process a real deployment
// runs, exercised end to end through a real browser in acceptance.spec.ts.
export default async function globalSetup(): Promise<void> {
  const workspaceDir = mkdtempSync(path.join(tmpdir(), 'pgos-e2e-'))
  const databasePath = path.join(workspaceDir, 'graph.db')

  const serverProcess = spawn(
    'uv',
    ['run', 'python', '-m', 'personal_graph_os.api'],
    {
      cwd: REPO_ROOT,
      env: {
        ...process.env,
        PGOS_DB_PATH: databasePath,
        PGOS_PORT: String(ACCEPTANCE_PORT),
        PGOS_STATIC_DIR: FRONTEND_DIST,
        PGOS_TRUSTED_HOSTS: '127.0.0.1,localhost',
      },
      stdio: 'ignore',
    },
  )

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
  )
}
