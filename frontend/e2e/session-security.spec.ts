import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'
import { STATE_PATH } from './global-setup.js'

interface AcceptanceState {
  port: number
  token: string
}

function loadState(): AcceptanceState {
  return JSON.parse(readFileSync(STATE_PATH, 'utf-8')) as AcceptanceState
}

// ST08-F01/F02: the unit/jsdom tests in frontend/src cover the storage primitives in
// isolation; only a real second browsing context (a genuine reload vs. an opener-created
// tab) can prove the actual isolation contract, which is what these two tests exist for.
test.describe('ST-08 session security acceptance (real server, real browser)', () => {
  test('a network failure during unlock persists nothing; reload stays locked (ST08-F01)', async ({
    page,
  }) => {
    const { port } = loadState()

    await page.route('**/workspace', (route) => route.abort())
    await page.goto(`http://127.0.0.1:${port}/app/`)
    await page.getByLabel(/access token/i).fill('a-candidate-that-is-never-verified')
    await page.getByRole('button', { name: /^unlock$/i }).click()

    await expect(page.getByText(/is it running/i)).toBeVisible()

    // The failed candidate must never have reached persisted state (`history.state`):
    // confirm directly, then prove a reload still shows the unlock screen.
    const persistedState = await page.evaluate(() => window.history.state)
    expect(persistedState).toBeNull()

    await page.unroute('**/workspace')
    await page.reload()
    await expect(page.getByLabel(/access token/i)).toBeVisible()
  })

  test('reload keeps this tab unlocked; an opener-created new tab starts locked (ST08-F02)', async ({
    page,
    context,
  }) => {
    const { port, token } = loadState()

    await page.goto(`http://127.0.0.1:${port}/app/`)
    await page.getByLabel(/access token/i).fill(token)
    await page.getByRole('button', { name: /^unlock$/i }).click()
    await expect(page.getByPlaceholder(/capture a task, note, or link/i)).toBeVisible()

    // Same tab, real reload: the verified token was committed, so it stays unlocked.
    await page.reload()
    await expect(page.getByPlaceholder(/capture a task, note, or link/i)).toBeVisible()

    // A new tab opened via `window.open` from this already-unlocked tab has an opener
    // relationship but always starts with an empty history entry: it must show the unlock
    // screen and carry no token, even though `sessionStorage` would have been cloned here.
    const popupPromise = context.waitForEvent('page')
    await page.evaluate(() => window.open(window.location.href))
    const popup = await popupPromise
    await popup.waitForLoadState()

    await expect(popup.getByLabel(/access token/i)).toBeVisible()
    const popupHistoryState = await popup.evaluate(() => window.history.state)
    expect(popupHistoryState).toBeNull()
  })
})
