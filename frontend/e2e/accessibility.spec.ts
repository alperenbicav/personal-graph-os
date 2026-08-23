import { execSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { AxeBuilder } from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { STATE_PATH } from './global-setup.js'

interface AcceptanceState {
  port: number
  token: string
}

function loadState(): AcceptanceState {
  return JSON.parse(readFileSync(STATE_PATH, 'utf-8')) as AcceptanceState
}

const FRONTEND_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

// ST-09 09.2: automated accessibility acceptance across every listed desktop state, at
// both required viewports (see playwright.config.ts's two projects), against the real
// packaged server/build.
async function assertNoSeriousOrCriticalViolations(page: Page, label: string) {
  const results = await new AxeBuilder({ page }).analyze()
  const blocking = results.violations.filter(
    (violation) => violation.impact === 'serious' || violation.impact === 'critical',
  )
  expect(blocking, `${label}: ${JSON.stringify(blocking, null, 2)}`).toEqual([])
}

async function navigateTo(page: Page, tabName: string) {
  await page
    .getByRole('navigation', { name: /workspace views/i })
    .getByRole('button', { name: tabName, exact: true })
    .click()
}

test.describe('ST-09.2 accessibility acceptance (real server, real build)', () => {
  // ST09-F05: a real second service-worker version, not a faked one. `index.html` carries
  // an inert `%VITE_BUILD_MARKER%` placeholder (read by no runtime code) purely so a
  // rebuild with a different marker value produces byte-different precached output — real
  // production upgrades change the same precache manifest for the same underlying reason
  // (new shell/asset content). `_AppStaticFiles` serves `index.html`/`sw.js` with
  // `Cache-Control: no-cache`, so the already-running server picks up the rebuilt files on
  // its very next request with no restart. This test rebuilds `frontend/dist` twice — once
  // to establish a known "version A" the page's service worker actually controls, once to
  // become "version B" — and always rebuilds once more in `finally` to leave the canonical
  // marker-free dist behind for the rest of this run. Runs first in this file, before any
  // other test captures data into the shared workspace this whole spec file's tests share.
  test('update-prompt state has no serious/critical automated axe violation and is keyboard-operable', async ({
    page,
  }) => {
    test.setTimeout(60_000)
    const { port, token } = loadState()

    execSync('npm run build', { cwd: FRONTEND_DIR, stdio: 'ignore' })
    try {
      await page.goto(`http://127.0.0.1:${port}/app/`)
      await page.getByLabel(/access token/i).fill(token)
      await page.getByRole('button', { name: /^unlock$/i }).click()
      await expect(page.getByPlaceholder(/new node title/i)).toBeVisible()
      // Confirm version A's service worker is actually installed and controlling this page
      // before rebuilding — otherwise the next build's registration would just be a first
      // install, never a real "update".
      await page.evaluate(() => navigator.serviceWorker.ready)

      execSync('npm run build', {
        cwd: FRONTEND_DIR,
        env: { ...process.env, VITE_BUILD_MARKER: `pgos-e2e-update-${Date.now()}` },
        stdio: 'ignore',
      })
      await page.evaluate(async () => {
        const registration = await navigator.serviceWorker.getRegistration()
        await registration?.update()
      })

      await expect(page.getByText(/a new version is available/i)).toBeVisible({
        timeout: 15_000,
      })
      await assertNoSeriousOrCriticalViolations(page, 'update-prompt')

      const reloadButton = page.getByRole('button', { name: /^reload$/i })
      const dismissButton = page.getByRole('button', { name: /^dismiss$/i })
      await expect(reloadButton).toBeVisible()
      await expect(dismissButton).toBeVisible()

      // Keyboard-operable: Dismiss reachable and functional with no pointer.
      await dismissButton.focus()
      await page.keyboard.press('Enter')
      await expect(page.getByText(/a new version is available/i)).toBeHidden()
    } finally {
      execSync('npm run build', { cwd: FRONTEND_DIR, stdio: 'ignore' })
    }
  })

  test('every primary state has no serious/critical automated axe violation', async ({ page }) => {
    const { port, token } = loadState()

    await page.goto(`http://127.0.0.1:${port}/app/`)
    await assertNoSeriousOrCriticalViolations(page, 'unlock')

    await page.getByLabel(/access token/i).fill(token)
    await page.getByRole('button', { name: /^unlock$/i }).click()
    const titleInput = page.getByPlaceholder(/new node title/i)
    await expect(titleInput).toBeVisible()

    // Two created nodes: enough for a real canvas, typed create flows, a real drag-to-connect
    // gesture, and an inspector selection (ST-08: per-tab typed create replaces the old global
    // quick-capture bar).
    const firstTitle = `Accessibility node A ${Date.now()}`
    await titleInput.fill(firstTitle)
    await page.getByRole('button', { name: /add node/i }).click()
    await expect(page.getByText(firstTitle).first()).toBeVisible()

    const secondTitle = `Accessibility node B ${Date.now()}`
    await titleInput.fill(secondTitle)
    await page.getByRole('button', { name: /add node/i }).click()
    await expect(page.getByText(secondTitle).first()).toBeVisible()

    await assertNoSeriousOrCriticalViolations(page, 'graph canvas')

    // Object selection + inspector: click the canvas's keyboard-operable object-list entry
    // (ST-09 09.2's synchronized listbox), not a raw React Flow node — this proves the
    // accessible surface itself is what's under test, not an incidental pointer target.
    await page.getByRole('option', { name: new RegExp(firstTitle) }).click()
    await expect(page.getByRole('heading', { name: firstTitle })).toBeVisible()
    await assertNoSeriousOrCriticalViolations(page, 'inspector (object selected)')

    // Real drag-to-connect gesture between the two rendered node cards' React Flow
    // handles — not a mock — to open the connect dialog the way a real user would. Scoped
    // by this test's own captured titles, not a raw `.pg-node` count: the canvas can carry
    // nodes from other specs sharing this run's server/workspace.
    const firstCard = page.locator('.pg-node', { hasText: firstTitle })
    const secondCard = page.locator('.pg-node', { hasText: secondTitle })
    await expect(firstCard).toHaveCount(1)
    await expect(secondCard).toHaveCount(1)
    const sourceHandle = firstCard.locator('.react-flow__handle.source')
    const targetHandle = secondCard.locator('.react-flow__handle.target')
    const sourceBox = await sourceHandle.boundingBox()
    const targetBox = await targetHandle.boundingBox()
    if (!sourceBox || !targetBox) throw new Error('Connection handle had no bounding box')
    await page.mouse.move(sourceBox.x + sourceBox.width / 2, sourceBox.y + sourceBox.height / 2)
    await page.mouse.down()
    await page.mouse.move(targetBox.x + targetBox.width / 2, targetBox.y + targetBox.height / 2, { steps: 15 })
    await page.mouse.up()
    await expect(page.getByRole('dialog', { name: /connect two objects/i })).toBeVisible()
    await assertNoSeriousOrCriticalViolations(page, 'connect dialog')
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog', { name: /connect two objects/i })).toBeHidden()

    // Schema dialog.
    await page.getByRole('button', { name: /^schema$/i }).click()
    await expect(page.getByRole('dialog', { name: /edit schema/i })).toBeVisible()
    await assertNoSeriousOrCriticalViolations(page, 'schema dialog')
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog', { name: /edit schema/i })).toBeHidden()

    await navigateTo(page, 'Tasks')
    await assertNoSeriousOrCriticalViolations(page, 'tasks view')

    await navigateTo(page, 'Wiki')
    await assertNoSeriousOrCriticalViolations(page, 'wiki view')

    await page.getByRole('button', { name: /search or jump/i }).click()
    const searchPalette = page.getByRole('dialog', { name: /command palette/i })
    await searchPalette.getByLabel(/command palette search/i).fill(firstTitle.split(' ')[0])
    await assertNoSeriousOrCriticalViolations(page, 'command palette search')
    await page.keyboard.press('Escape')
    await expect(searchPalette).toBeHidden()

    await navigateTo(page, 'Research')
    await assertNoSeriousOrCriticalViolations(page, 'research view')

    // ST-06 review finding S6-F02/F03 follow-up: the Research filter bar and workflow-bucket
    // chips are new interactive controls in this same view, scanned as part of the same state.
    await page.getByRole('button', { name: /^all$/i }).click()
    await assertNoSeriousOrCriticalViolations(page, 'research view (all bucket filter)')

    await navigateTo(page, 'Repositories')
    await assertNoSeriousOrCriticalViolations(page, 'repositories view')

    await navigateTo(page, 'Activity')
    await assertNoSeriousOrCriticalViolations(page, 'activity/undo view')

    // Offline/reconnect: the same states `pwa.spec.ts` already exercises for functional
    // behavior, scanned here for accessibility too (an alert/status role change must stay
    // accessible, not just present).
    await page.context().setOffline(true)
    await page.reload()
    await expect(page.getByText(/could not reach the personal graph os api/i)).toBeVisible()
    await assertNoSeriousOrCriticalViolations(page, 'offline state')
    await page.context().setOffline(false)
  })

  test('unlock, capture, object selection, inspector edit, search selection, and dialog confirm/cancel all work with no pointer', async ({
    page,
  }) => {
    const { port, token } = loadState()
    await page.goto(`http://127.0.0.1:${port}/app/`)

    // Keyboard-only unlock: the token field autofocuses on load, so typing starts there
    // directly; Tab reaches Unlock next, then Enter submits.
    await expect(page.getByLabel(/access token/i)).toBeFocused()
    await page.keyboard.type(token)
    await page.keyboard.press('Tab')
    await expect(page.getByRole('button', { name: /^unlock$/i })).toBeFocused()
    await page.keyboard.press('Enter')

    const titleInput = page.getByPlaceholder(/new node title/i)
    await expect(titleInput).toBeVisible()

    // Keyboard-only typed create (ST-08): focus the Graph tab's title input directly (its exact
    // tab-order position depends on unrelated toolbar controls, which is not what this flow is
    // proving) and submit with Enter.
    const title = `Keyboard-only node ${Date.now()}`
    await titleInput.focus()
    await titleInput.fill(title)
    await page.keyboard.press('Enter')
    await expect(page.getByText(title).first()).toBeVisible()

    // Keyboard-only object selection via the canvas's synchronized keyboard listbox.
    const option = page.getByRole('option', { name: new RegExp(title) })
    await option.focus()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('heading', { name: title })).toBeVisible()

    // Keyboard-only inspector edit: the status select is a real, labeled form control that
    // must actually be present — a captured node always gets a default type with statuses
    // (Note, per `default_schema.py`), so this is a real assertion, not a conditional skip.
    const statusSelect = page.getByLabel(/^status$/i)
    await expect(statusSelect).toBeVisible()
    await statusSelect.focus()
    await page.keyboard.press('ArrowDown')

    // Keyboard-only search + result selection.
    await page.keyboard.press('Meta+k')
    const searchInput = page.getByLabel(/command palette search/i)
    await searchInput.focus()
    await searchInput.fill(title.split(' ')[0])
    const resultOption = page.getByRole('option', { name: new RegExp(title) }).first()
    await expect(resultOption).toBeVisible()
    await page.keyboard.press('Enter')

    // Keyboard-only undo: open Activity, expand the most recent event (its row shows
    // actor/action/entity type, not the node title — the most recent event here is the
    // status update just made above, which must be undoable) with the reason field and
    // Confirm undo button, all via keyboard. Asserted directly, not conditionally skipped:
    // a future regression that stops rendering the undo affordance must fail this test.
    await navigateTo(page, 'Activity')
    const captureRow = page.locator('.activity-row').first()
    await captureRow.focus()
    await page.keyboard.press('Enter')
    const undoButton = page.getByRole('button', { name: /^undo$/i }).first()
    await expect(undoButton).toBeVisible()
    await undoButton.focus()
    await page.keyboard.press('Enter')
    const reasonInput = page.getByLabel(/reason for undo/i)
    await reasonInput.focus()
    await reasonInput.fill('Keyboard-only accessibility acceptance')
    await page.getByRole('button', { name: /confirm undo/i }).focus()
    await page.keyboard.press('Enter')
    await expect(page.getByText(/^undone\.$/i)).toBeVisible()

    // Keyboard-only dialog confirm/cancel: open the schema dialog, Escape-cancel it.
    await page.getByRole('button', { name: /^schema$/i }).focus()
    await page.keyboard.press('Enter')
    const schemaDialog = page.getByRole('dialog', { name: /edit schema/i })
    await expect(schemaDialog).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(schemaDialog).toBeHidden()
  })
})
