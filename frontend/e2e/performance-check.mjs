#!/usr/bin/env node
// ST-09.3/ST09-F03/ST09-F06: the browser-side half of the performance acceptance gate —
// measures unlock-to-primary-controls and per-projection switch time against a server the
// caller has already started and seeded with the representative fixture, using the same
// two-warmup/twenty-sample/p95 methodology as the backend budgets
// (`tests/acceptance/performance_fixture.py`), not a single lucky observation.
//
// Deliberately NOT a Playwright *test* file: `playwright test` always runs
// `playwright.config.ts`'s globalSetup/globalTeardown, which spawns its own separate,
// freshly-empty acceptance server — exactly what this script must NOT do, since it needs
// to measure the caller's already-running, already-seeded server instead. This is a plain
// Node script driving `playwright-core`'s browser launcher directly.
//
// Usage: node e2e/performance-check.mjs <baseUrl>, with the bearer token piped on stdin
// (never as a command-line argument, which would be visible to any local process listing —
// ST09-F06). Prints one sanitized JSON object to stdout: sample count/p95/budget/pass per
// metric, never raw samples, titles, or the token itself. Exit code 0 if every budget
// passes, 1 otherwise.

import { chromium } from 'playwright-core'
import { budgetResult } from './percentile.mjs'

const WARMUP_SAMPLES = 2
const TIMED_SAMPLES = 20
const UNLOCK_TO_CONTROLS_BUDGET_SECONDS = 4
const VIEW_SWITCH_BUDGET_SECONDS = 2
const PROJECTION_TABS = ['Table', 'Kanban', 'Timeline']

async function readTokenFromStdin() {
  const chunks = []
  for await (const chunk of process.stdin) chunks.push(chunk)
  const token = Buffer.concat(chunks).toString('utf-8').trim()
  if (!token) throw new Error('no bearer token received on stdin')
  return token
}

// Each sample opens a brand-new browser tab (context page) rather than reloading the same
// one: ST-08's tab isolation means a reload of an already-unlocked tab stays unlocked (see
// `session-security.spec.ts`), so only a fresh tab reproduces a real "cold" unlock.
async function measureUnlockToControls(context, baseUrl, token) {
  const samples = []
  for (let i = 0; i < WARMUP_SAMPLES + TIMED_SAMPLES; i++) {
    const page = await context.newPage()
    try {
      await page.goto(`${baseUrl}/app/`)
      const started = Date.now()
      await page.getByLabel(/access token/i).fill(token)
      await page.getByRole('button', { name: /^unlock$/i }).click()
      await page.getByPlaceholder(/capture a task, note, or link/i).waitFor({ state: 'visible' })
      const elapsedSeconds = (Date.now() - started) / 1000
      if (i >= WARMUP_SAMPLES) samples.push(elapsedSeconds)
    } finally {
      await page.close()
    }
  }
  return samples
}

async function clickTab(page, tabName) {
  await page
    .getByRole('navigation', { name: /workspace views/i })
    .getByRole('button', { name: tabName, exact: true })
    .click()
}

// Switches away to a different projection tab first so switching back into `tabName` is a
// fresh transition each sample, not a no-op re-click of the already-active tab.
async function measureViewSwitch(page, tabName) {
  const awayTab = PROJECTION_TABS.find((name) => name !== tabName)
  const samples = []
  for (let i = 0; i < WARMUP_SAMPLES + TIMED_SAMPLES; i++) {
    await clickTab(page, awayTab)
    await page.locator('.node-row').first().waitFor({ state: 'visible' })
    const started = Date.now()
    await clickTab(page, tabName)
    // "Usable" means the projection actually rendered fixture rows, not just that the tab
    // switched — the real signal a user waits for.
    await page.locator('.node-row').first().waitFor({ state: 'visible' })
    const elapsedSeconds = (Date.now() - started) / 1000
    if (i >= WARMUP_SAMPLES) samples.push(elapsedSeconds)
  }
  return samples
}

async function main() {
  const [, , baseUrl] = process.argv
  if (!baseUrl) {
    console.error('Usage: node e2e/performance-check.mjs <baseUrl> (token piped on stdin)')
    process.exit(2)
  }
  const token = await readTokenFromStdin()

  const browser = await chromium.launch({ headless: true })
  const context = await browser.newContext()
  const results = {}
  let allPassed = true

  try {
    const unlockSamples = await measureUnlockToControls(context, baseUrl, token)
    const unlockResult = budgetResult(
      'unlock_to_controls',
      unlockSamples,
      UNLOCK_TO_CONTROLS_BUDGET_SECONDS,
    )
    results.unlock_to_controls = unlockResult
    if (!unlockResult.passed) allPassed = false

    // View-switch sampling reuses one already-unlocked page/tab across all three
    // projections — only the unlock metric above needs a fresh tab per sample.
    const page = await context.newPage()
    try {
      await page.goto(`${baseUrl}/app/`)
      await page.getByLabel(/access token/i).fill(token)
      await page.getByRole('button', { name: /^unlock$/i }).click()
      await page.getByPlaceholder(/capture a task, note, or link/i).waitFor({ state: 'visible' })
      await clickTab(page, PROJECTION_TABS[0])
      await page.locator('.node-row').first().waitFor({ state: 'visible' })

      results.view_switch = {}
      for (const tabName of PROJECTION_TABS) {
        const samples = await measureViewSwitch(page, tabName)
        const tabResult = budgetResult(tabName.toLowerCase(), samples, VIEW_SWITCH_BUDGET_SECONDS)
        results.view_switch[tabName.toLowerCase()] = tabResult
        if (!tabResult.passed) allPassed = false
      }
    } finally {
      await page.close()
    }
  } finally {
    await context.close()
    await browser.close()
  }

  console.log(JSON.stringify(results))
  process.exit(allPassed ? 0 : 1)
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
