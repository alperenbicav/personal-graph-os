import http from 'node:http'
import { readFileSync } from 'node:fs'
import { AxeBuilder } from '@axe-core/playwright'
import { expect, test } from '@playwright/test'
import { STATE_PATH } from './global-setup.js'

interface AcceptanceState {
  port: number
  token: string
}

function loadState(): AcceptanceState {
  return JSON.parse(readFileSync(STATE_PATH, 'utf-8')) as AcceptanceState
}

// `fetch`/Playwright's `request` fixture both forbid overriding the `Host` header (Fetch
// standard); the disallowed-host check below needs the raw header, so it goes through
// Node's `http` module directly instead of through the browser.
function rawRequest(options: http.RequestOptions): Promise<{ status: number }> {
  return new Promise((resolve, reject) => {
    const req = http.request(options, (res) => {
      res.resume()
      resolve({ status: res.statusCode ?? 0 })
    })
    req.on('error', reject)
    req.end()
  })
}

test.describe('ST-08.3 remote-access acceptance (real server, real build)', () => {
  test('unlocks, completes an authenticated read and mutation, and exports the workspace', async ({
    page,
  }) => {
    const { port, token } = loadState()

    await page.goto(`http://127.0.0.1:${port}/app/`)
    await page.getByLabel(/access token/i).fill(token)
    await page.getByRole('button', { name: /^unlock$/i }).click()

    // Unlock succeeded: the token screen is gone and the Graph tab's typed create-node input
    // (ST-08: the TopBar global capture was replaced by per-tab typed create flows) is visible.
    await expect(page.getByLabel(/access token/i)).toBeHidden()
    const titleInput = page.getByPlaceholder(/new node title/i)
    await expect(titleInput).toBeVisible()

    const title = `E2E acceptance node ${Date.now()}`
    await titleInput.fill(title)
    await page.getByRole('button', { name: /add node/i }).click()
    await expect(page.getByText(title).first()).toBeVisible()

    const downloadPromise = page.waitForEvent('download')
    await page.getByRole('button', { name: /download export/i }).click()
    const download = await downloadPromise
    expect(download.suggestedFilename()).toMatch(/\.zip$/)
  })

  test('rejects an invalid token and never unlocks', async ({ page }) => {
    const { port } = loadState()

    await page.goto(`http://127.0.0.1:${port}/app/`)
    await page.getByLabel(/access token/i).fill('definitely-the-wrong-token')
    await page.getByRole('button', { name: /^unlock$/i }).click()

    await expect(page.getByText(/token was rejected/i)).toBeVisible()
    await expect(page.getByLabel(/access token/i)).toBeVisible()
  })

  test('navigates to Agents view, creates custom agent, triggers execution, and displays dock event', async ({
    page,
  }) => {
    const { port, token } = loadState()

    await page.goto(`http://127.0.0.1:${port}/app/`)
    await page.getByLabel(/access token/i).fill(token)
    await page.getByRole('button', { name: /^unlock$/i }).click()
    await expect(page.getByLabel(/access token/i)).toBeHidden()

    // Verify live agent indicator pill is present in topbar
    const livePill = page.getByLabel(/active agents indicator/i)
    await expect(livePill).toBeVisible()

    // Navigate to Agents tab
    await page
      .getByRole('navigation', { name: /workspace views/i })
      .getByRole('button', { name: 'Agents' })
      .click()

    await expect(page.getByText('Autonomous Agents Fleet')).toBeVisible()
    await expect(page.getByRole('button', { name: /\+ create agent/i })).toBeVisible()

    // Create a new custom agent
    await page.getByRole('button', { name: /\+ create agent/i }).click()
    const modal = page.getByRole('dialog', { name: /create new agent/i })
    await expect(modal).toBeVisible()

    const agentName = `Synthesis-Agent-${Date.now()}`
    await page.getByLabel('Agent Name').fill(agentName)
    await page.getByLabel('Agent Emoji').fill('🔬')
    await page.getByLabel('Agent System Prompt').fill('Autonomous research synthesizer for graph nodes.')
    await modal.getByRole('button', { name: /^create agent$/i }).click()

    // Verify newly created agent appears in the fleet grid
    await expect(page.getByText(agentName)).toBeVisible()

    // Mock LLM status, message execution and runs response
    await page.route(/\/agents\/status/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          configured: true,
          provider: 'openai',
          model: 'gpt-4o',
          available_agents_count: 3,
          unconfigured_reason: null,
        }),
      })
    })

    let runCreated = false
    await page.route(/\/agents\/.*\/message/, async (route) => {
      runCreated = true
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          agent_id: 'mock-agent',
          response: 'Synthesized 5 graph nodes into actionable research takeaways.',
          run_id: `run-${Date.now()}`,
          action: 'synthesize_takeaways',
          status: 'applied',
        }),
      })
    })

    await page.route(/\/agents\/.*runs/, async (route) => {
      if (runCreated) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify([
            {
              id: `run-${Date.now()}`,
              agent_id: 'mock-agent',
              action: 'synthesize_takeaways',
              entity_type: 'resource',
              entity_id: 'res-e2e',
              status: 'applied',
              summary: 'synthesized 5 graph nodes into takeaways',
              diff_json: JSON.stringify({ additions: 5 }),
              created_at: new Date().toISOString(),
            },
          ]),
        })
      } else {
        await route.continue()
      }
    })

    // Open the Agent Dock via Topbar live pill indicator
    await livePill.click()
    const dock = page.getByRole('complementary', { name: /agent stream & dock/i })
    await expect(dock).toBeVisible()

    // Dispatch prompt from Agent Dock composer
    const composer = dock.getByLabel('Ask Agent Prompt')
    await composer.fill('Synthesize recent nodes')
    await composer.press('Enter')

    // Verify run event appears in the dock
    await expect(page.getByText(/synthesized 5 graph nodes into takeaways/i).first()).toBeVisible({
      timeout: 10_000,
    })

    // Verify accessibility on the Agents view and open dock
    const axeResults = await new AxeBuilder({ page }).analyze()
    const blocking = axeResults.violations.filter(
      (v) => v.impact === 'serious' || v.impact === 'critical',
    )
    expect(blocking, `Agents view axe violations: ${JSON.stringify(blocking, null, 2)}`).toEqual([])
  })

  test('rejects a request with no bearer token', async () => {
    const { port } = loadState()
    const result = await rawRequest({ host: '127.0.0.1', port, path: '/workspace' })
    expect(result.status).toBe(401)
  })

  test('rejects a request whose Host header is not in the trusted-host allowlist', async () => {
    const { port, token } = loadState()
    const result = await rawRequest({
      host: '127.0.0.1',
      port,
      path: '/workspace',
      headers: { Host: 'evil.example.com', Authorization: `Bearer ${token}` },
    })
    expect(result.status).toBe(400)
  })
})

