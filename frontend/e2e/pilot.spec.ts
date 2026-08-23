// ST09-F02: the one real packaged-browser pilot that drives the planned UI mutations in a
// single workflow (not split across specs that each only touch a slice), verifies the
// resulting canonical state, then performs a real stop/backup/restore/restart and verifies
// representative recovered state through the UI as well as REST — at both required desktop
// viewports (this file gets both of `playwright.config.ts`'s projects automatically, the
// same as every other spec).
//
// This test manages its own two servers directly (never the shared one `global-setup.ts`
// spawns for the rest of `e2e/`): it needs to stop a server, back it up, and start a second
// one against a restored database, which the shared long-lived server can't do without
// disrupting every other spec sharing it.
import { type ChildProcess, execFileSync } from 'node:child_process'
import { mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs'
import { createServer } from 'node:net'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test, type Page } from '@playwright/test'
import { spawnAndAwaitReady, terminateProcess } from './pilotServerLifecycle.ts'

const FRONTEND_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const REPO_ROOT = path.resolve(FRONTEND_DIR, '..')
const FRONTEND_DIST = path.join(FRONTEND_DIR, 'dist')

async function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer()
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      if (address === null || typeof address === 'string') {
        reject(new Error('could not determine a free port'))
        return
      }
      const { port } = address
      server.close(() => resolve(port))
    })
    server.on('error', reject)
  })
}

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

async function readToken(tokenPath: string, timeoutMs = 5_000): Promise<string> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const token = readFileSync(tokenPath, 'utf-8').trim()
      if (token) return token
    } catch {
      // Not written yet.
    }
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  throw new Error(`bearer token file never appeared at ${tokenPath}`)
}

interface PilotServer {
  process: ChildProcess
  baseUrl: string
  token: string
}

async function spawnPilotServer(databasePath: string): Promise<PilotServer> {
  const port = await freePort()
  const tokenPath = path.join(path.dirname(databasePath), 'api-token')
  const baseUrl = `http://127.0.0.1:${port}`
  let token = ''
  // ST09-F07: `spawnAndAwaitReady` terminates (and awaits) the spawned process if readiness
  // or token discovery below throws, so this function never returns a `PilotServer` for a
  // process that's still running — there's no leaked child for a caller that never gets a
  // handle back to clean up.
  const serverProcess = await spawnAndAwaitReady(
    'uv',
    ['run', 'python', '-m', 'personal_graph_os.api'],
    {
      cwd: REPO_ROOT,
      env: {
        ...process.env,
        PGOS_DB_PATH: databasePath,
        PGOS_PORT: String(port),
        PGOS_STATIC_DIR: FRONTEND_DIST,
        PGOS_TRUSTED_HOSTS: '127.0.0.1,localhost',
      },
      stdio: 'ignore',
    },
    async () => {
      await waitForServer(`${baseUrl}/app/`)
      token = await readToken(tokenPath)
    },
  )
  return { process: serverProcess, baseUrl, token }
}

async function stopPilotServer(server: PilotServer): Promise<void> {
  // ST09-F07: awaits real process exit (escalating past an ignored SIGTERM) instead of
  // firing SIGTERM and returning immediately, so "stopped" is a guarantee the caller can
  // safely back up the workspace after, not a race with the OS's own signal delivery.
  await terminateProcess(server.process)
}

async function navigateTo(page: Page, tabName: string) {
  await page
    .getByRole('navigation', { name: /workspace views/i })
    .getByRole('button', { name: tabName, exact: true })
    .click()
}

async function unlock(page: Page, baseUrl: string, token: string) {
  await page.goto(`${baseUrl}/app/`)
  await page.getByLabel(/access token/i).fill(token)
  await page.getByRole('button', { name: /^unlock$/i }).click()
  await expect(page.getByPlaceholder(/new node title/i)).toBeVisible()
}

async function captureNode(page: Page, typeName: string, title: string) {
  await page.getByLabel(/^node type$/i).selectOption({ label: typeName })
  const titleInput = page.getByPlaceholder(/new node title/i)
  await titleInput.fill(title)
  await page.getByRole('button', { name: /add node/i }).click()
  await expect(page.getByText(title).first()).toBeVisible()
}

test.describe('ST-09.4 complete pilot journey and recovery (real server, real build)', () => {
  test('drives every planned UI mutation, verifies canonical state, then restores and re-verifies through the UI', async ({
    page,
  }) => {
    test.setTimeout(120_000)

    const workspaceDirA = mkdtempSync(path.join(tmpdir(), 'pgos-pilot-workspace-a-'))
    const backupsDir = path.join(mkdtempSync(path.join(tmpdir(), 'pgos-pilot-backups-')), 'out')
    let workspaceDirB: string | null = null
    let serverA: PilotServer | null = null
    let serverB: PilotServer | null = null

    const customTypeName = `Pilot Type ${Date.now()}`
    const customFieldName = 'Pilot custom field'
    const customStatusName = 'pilot_reviewed'
    const sourceTitle = `Pilot source node ${Date.now()}`
    const targetTitle = `Pilot target node ${Date.now()}`
    const markerTitle = `Pilot canonical marker ${Date.now()}`
    const discoveryCandidateUrl = `https://example.com/pilot-discovery-${Date.now()}`
    const attachmentContent = 'pilot attachment content'
    const attachmentFileName = 'pilot-attachment.txt'

    try {
      serverA = await spawnPilotServer(path.join(workspaceDirA, 'graph.db'))
      await unlock(page, serverA.baseUrl, serverA.token)

      // 1. Custom schema: a new node type with a custom field and status, all through the
      // real Schema dialog (not REST).
      await page.getByRole('button', { name: /^schema$/i }).click()
      const schemaDialog = page.getByRole('dialog', { name: /edit schema/i })
      await expect(schemaDialog).toBeVisible()
      await schemaDialog.getByPlaceholder('New node type name').fill(customTypeName)
      await schemaDialog.getByRole('button', { name: /^create$/i }).click()
      await expect(schemaDialog.getByText(customTypeName)).toBeVisible()
      await schemaDialog.getByRole('button', { name: customTypeName }).click()
      await schemaDialog.getByPlaceholder('Field name').fill(customFieldName)
      await schemaDialog.getByRole('button', { name: /^add field$/i }).click()
      await expect(schemaDialog.getByText(customFieldName)).toBeVisible()
      await schemaDialog.getByPlaceholder('Status name').fill(customStatusName)
      await schemaDialog.getByRole('button', { name: /^add status$/i }).click()
      await expect(schemaDialog.getByText(customStatusName)).toBeVisible()
      await page.getByRole('button', { name: /^close$/i }).click()
      await expect(schemaDialog).toBeHidden()

      // 2. Capture two nodes — the target uses the just-created custom type, proving the
      // schema change is immediately usable for real capture, not just visible in the editor.
      await captureNode(page, 'Note', sourceTitle)
      await captureNode(page, customTypeName, targetTitle)

      // 3. Confirmed connect (not cancelled): a real drag-to-connect gesture between the two
      // rendered node cards, then Connect (not Escape). Select the source node first (as the
      // accessibility spec's proven-working drag does) rather than leaving whichever node was
      // captured last selected.
      await page.getByRole('option', { name: new RegExp(sourceTitle) }).click()
      await expect(page.getByRole('heading', { name: sourceTitle })).toBeVisible()
      const sourceCard = page.locator('.pg-node', { hasText: sourceTitle })
      const targetCard = page.locator('.pg-node', { hasText: targetTitle })
      await expect(sourceCard).toHaveCount(1)
      await expect(targetCard).toHaveCount(1)
      const sourceHandle = sourceCard.locator('.react-flow__handle.source')
      const targetHandle = targetCard.locator('.react-flow__handle.target')
      const sourceBox = await sourceHandle.boundingBox()
      const targetBox = await targetHandle.boundingBox()
      if (!sourceBox || !targetBox) throw new Error('Connection handle had no bounding box')
      await page.mouse.move(sourceBox.x + sourceBox.width / 2, sourceBox.y + sourceBox.height / 2)
      await page.mouse.down()
      await page.mouse.move(
        targetBox.x + targetBox.width / 2,
        targetBox.y + targetBox.height / 2,
        { steps: 15 },
      )
      await page.mouse.up()
      const connectDialog = page.getByRole('dialog', { name: /connect two objects/i })
      await expect(connectDialog).toBeVisible()
      // Real keyboard confirm (focus + Enter), not a pointer click — proves the modal is
      // operable without a mouse. `accessibility.spec.ts` proves the Escape-cancel path.
      const connectButton = connectDialog.getByRole('button', { name: /^connect$/i })
      await connectButton.focus()
      await connectButton.press('Enter')
      await expect(connectDialog).toBeHidden()
      // The confirmed edge is real: it renders on the canvas immediately.
      await expect(page.locator('.react-flow__edge')).toHaveCount(1)

      // 4. Place: a new canvas, then explicitly placing an already-captured node onto it via
      // the real "existing object to place" control — not just relying on capture's own
      // auto-placement on the canvas active at capture time. "+ New canvas" opens the styled
      // NewCanvasModal (ST-08 of the UX overhaul replaced window.prompt).
      await page.getByRole('button', { name: '+ New canvas' }).click()
      await page.getByLabel('Canvas name').fill('Pilot second canvas')
      await page.getByRole('button', { name: 'Create canvas' }).click()
      await expect(page.getByRole('button', { name: 'Pilot second canvas' })).toBeVisible()
      await page.getByLabel(/existing object to place on this canvas/i).selectOption({
        label: sourceTitle,
      })
      await page.getByRole('button', { name: /^place here$/i }).click()
      await expect(page.locator('.pg-node', { hasText: sourceTitle })).toHaveCount(1)

      // 5. Edit: back on the canvas that actually has the target node placed (it was never
      // placed on "Pilot second canvas"), select it and change its status through the real
      // Inspector control.
      await page.getByRole('button', { name: 'Main' }).click()
      await page.getByRole('option', { name: new RegExp(targetTitle) }).click()
      await expect(page.getByRole('heading', { name: targetTitle })).toBeVisible()
      const statusSelect = page.getByLabel(/^status$/i)
      const selectedValues = await statusSelect.selectOption({ label: customStatusName })
      expect(selectedValues).toHaveLength(1)

      // 6. Files: upload a managed attachment through the real Inspector/FilesPanel file
      // input — not a REST call.
      await page.setInputFiles('input[type="file"]', {
        name: attachmentFileName,
        mimeType: 'text/plain',
        buffer: Buffer.from(attachmentContent, 'utf-8'),
      })
      await expect(page.getByText(attachmentFileName)).toBeVisible()

      // 7. Research: a real typed create through the Research view's form (ST-08: Discovery and
      // the global capture were removed; every writable tab owns a typed create flow) — the only
      // real way to create a backing `Resource`, not just a Node (product decision #1).
      await navigateTo(page, 'Research')
      await page.getByRole('button', { name: /new paper\/article/i }).click()
      const researchCreate = page.getByRole('form', { name: /create paper or article/i })
      await researchCreate.getByPlaceholder('Title').fill('Pilot discovery candidate')
      await researchCreate.getByPlaceholder(/source url/i).fill(discoveryCandidateUrl)
      await researchCreate.getByRole('button', { name: /^add$/i }).click()
      await expect(page.getByText('Pilot discovery candidate').first()).toBeVisible()

      // A distinctive marker node, created last, to search for by name after restore.
      await navigateTo(page, 'Graph')
      await captureNode(page, 'Note', markerTitle)

      // 8. Activity and safe undo: undo the status edit from step 5 through the real
      // Activity UI.
      await navigateTo(page, 'Activity')
      const statusUpdateRow = page.locator('.activity-row', { hasText: 'updated' }).first()
      await statusUpdateRow.click()
      const undoButton = page.getByRole('button', { name: /^undo$/i }).first()
      await expect(undoButton).toBeVisible()
      await undoButton.click()
      await page.getByLabel(/reason for undo/i).fill('Pilot journey undo verification')
      await page.getByRole('button', { name: /confirm undo/i }).click()
      await expect(page.getByText(/^undone\.$/i)).toBeVisible()

      // 9. Export: the real portable-export download.
      const downloadPromise = page.waitForEvent('download')
      await page.getByRole('button', { name: /download export/i }).click()
      const download = await downloadPromise
      expect(download.suggestedFilename()).toMatch(/\.zip$/)

      // Independent verification (ST09-F04-style): re-read real REST state rather than
      // trusting only the UI's own optimistic view, before tearing the server down.
      const restClientA = page.request
      const workspaceA = await (
        await restClientA.get(`${serverA.baseUrl}/workspace`, {
          headers: { Authorization: `Bearer ${serverA.token}` },
        })
      ).json()
      const pilotNodeTypeA = workspaceA.node_types.find(
        (nodeType: { name: string }) => nodeType.name === customTypeName,
      )
      expect(pilotNodeTypeA).toBeTruthy()
      expect(
        pilotNodeTypeA.field_definitions.some(
          (field: { name: string }) => field.name === customFieldName,
        ),
      ).toBe(true)
      const nodesA = await (
        await restClientA.get(`${serverA.baseUrl}/nodes`, {
          params: { workspace_id: workspaceA.id },
          headers: { Authorization: `Bearer ${serverA.token}` },
        })
      ).json()
      expect(nodesA.some((node: { title: string }) => node.title === markerTitle)).toBe(true)
      await stopPilotServer(serverA)

      // Real recovery drill: back up the stopped workspace, restore into a fresh empty
      // directory, and start a brand-new server against it with a freshly generated token —
      // never the original, and never reusing the original directory.
      execFileSync('uv', ['run', 'pgos-backup', 'create', workspaceDirA, backupsDir], {
        cwd: REPO_ROOT,
      })
      const [backupFileName] = readdirSync(backupsDir)
      const backupPath = path.join(backupsDir, backupFileName)
      workspaceDirB = path.join(
        mkdtempSync(path.join(tmpdir(), 'pgos-pilot-restore-parent-')),
        'restored',
      )
      execFileSync('uv', ['run', 'pgos-backup', 'restore', backupPath, workspaceDirB], {
        cwd: REPO_ROOT,
      })

      serverB = await spawnPilotServer(path.join(workspaceDirB, 'graph.db'))
      expect(serverB.token).not.toBe(serverA.token)

      const restoredPage = await page.context().newPage()
      await unlock(restoredPage, serverB.baseUrl, serverB.token)

      // Representative recovered state through the UI: the marker node is findable via
      // real Search, the custom schema still governs the Schema dialog, the attachment and
      // discovery-imported resource are both still visible, and the earlier undo is still
      // recorded.
      await navigateTo(restoredPage, 'Search')
      await restoredPage.locator('.search-view').getByLabel(/^search$/i).fill('Pilot canonical')
      await restoredPage.locator('.search-view').getByRole('button', { name: /^search$/i }).click()
      await expect(restoredPage.getByText(markerTitle).first()).toBeVisible()

      await restoredPage.getByRole('button', { name: /^schema$/i }).click()
      const restoredSchemaDialog = restoredPage.getByRole('dialog', { name: /edit schema/i })
      await expect(restoredSchemaDialog.getByText(customTypeName)).toBeVisible()
      await restoredSchemaDialog.getByRole('button', { name: customTypeName }).click()
      await expect(restoredSchemaDialog.getByText(customFieldName)).toBeVisible()
      await restoredPage.getByRole('button', { name: /^close$/i }).click()

      await navigateTo(restoredPage, 'Research')
      await expect(restoredPage.getByText('Pilot discovery candidate').first()).toBeVisible()

      await navigateTo(restoredPage, 'Graph')
      await restoredPage.getByRole('option', { name: new RegExp(targetTitle) }).click()
      await expect(restoredPage.getByText(attachmentFileName)).toBeVisible()

      await navigateTo(restoredPage, 'Activity')
      await expect(restoredPage.getByText(/reversed/i).first()).toBeVisible()

      // Export still works from the restored workspace.
      const restoredDownloadPromise = restoredPage.waitForEvent('download')
      await restoredPage.getByRole('button', { name: /download export/i }).click()
      const restoredDownload = await restoredDownloadPromise
      expect(restoredDownload.suggestedFilename()).toMatch(/\.zip$/)

      // REST cross-check, independent of what the UI happens to render.
      const restClientB = restoredPage.request
      const workspaceB = await (
        await restClientB.get(`${serverB.baseUrl}/workspace`, {
          headers: { Authorization: `Bearer ${serverB.token}` },
        })
      ).json()
      const nodesB = await (
        await restClientB.get(`${serverB.baseUrl}/nodes`, {
          params: { workspace_id: workspaceB.id },
          headers: { Authorization: `Bearer ${serverB.token}` },
        })
      ).json()
      expect(nodesB.some((node: { title: string }) => node.title === markerTitle)).toBe(true)

      await restoredPage.close()
    } finally {
      // Idempotent regardless of how far the flow got: `stopPilotServer` no-ops for an
      // already-exited process, so awaiting it again for server A here (already stopped
      // before the backup) is harmless — this `finally` is the single place that
      // guarantees cleanup no matter which step above threw, and each await really does
      // confirm the process is gone before moving on (ST09-F07).
      if (serverA) await stopPilotServer(serverA)
      if (serverB) await stopPilotServer(serverB)
      rmSync(workspaceDirA, { recursive: true, force: true })
      if (workspaceDirB) rmSync(path.dirname(workspaceDirB), { recursive: true, force: true })
      rmSync(path.dirname(backupsDir), { recursive: true, force: true })
    }
  })
})
