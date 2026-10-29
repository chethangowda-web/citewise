import { expect, test } from '@playwright/test'

// The real-backend test needs the admin key: E2E_ADMIN_KEY=<key> npx playwright test
const ADMIN_KEY = process.env.E2E_ADMIN_KEY

const openAudit = async (page: import('@playwright/test').Page) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Audit', exact: true }).click()
}

test('audit view asks for a key and rejects a wrong one', async ({ page }) => {
  await page.route('**/api/admin/**', (r) => r.fulfill({ status: 401, json: { detail: 'Invalid or missing admin key' } }))
  await openAudit(page)
  await expect(page.getByLabel('Admin key')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Unlock' })).toBeDisabled()
  await page.getByLabel('Admin key').fill('wrong-key')
  await page.getByRole('button', { name: 'Unlock' }).click()
  await expect(page.getByRole('alert')).toContainText('Invalid')
  await expect(page.getByLabel('Admin key')).toBeVisible() // stays locked, key not remembered
  expect(await page.evaluate(() => sessionStorage.getItem('policyDeskAdminKey'))).toBeNull()
})

test('audit view explains when admin access is disabled on the server', async ({ page }) => {
  await page.route('**/api/admin/**', (r) => r.fulfill({ status: 404, json: { detail: 'Not found' } }))
  await page.addInitScript(() => sessionStorage.setItem('policyDeskAdminKey', 'anything'))
  await openAudit(page)
  await expect(page.getByRole('alert')).toContainText('not enabled')
})

test('audit view shows stats, filters and expands entries (real backend)', async ({ page }) => {
  test.skip(!ADMIN_KEY, 'set E2E_ADMIN_KEY to run against the real admin API')
  await openAudit(page)
  await page.getByLabel('Admin key').fill(ADMIN_KEY!)
  await page.getByRole('button', { name: 'Unlock' }).click()

  await expect(page.getByTestId('stats')).toBeVisible()
  const total = Number(await page.getByTestId('stat-total').innerText())
  expect(total).toBeGreaterThan(0)
  expect(await page.getByTestId('ledger').locator('li').count()).toBeGreaterThan(0)
  await page.waitForTimeout(900) // let the outcome bar animate
  await page.screenshot({ path: 'shots/06-audit.png' })

  await page.getByRole('button', { name: 'blocked', exact: true }).click()
  await expect(page.locator('.ledger .entry').first()).toHaveClass(/e-blocked/)
  const classes = await page.locator('.ledger .entry').evaluateAll((els) => els.map((e) => e.className))
  expect(classes.every((c) => c.includes('e-blocked'))).toBe(true)

  await page.locator('.entry-head').first().click()
  await expect(page.locator('.entry-body')).toContainText(/prompt_injection|toxicity|too_long|empty/)
  await expect(page.locator('.entry-body')).toContainText('Request')

  await page.getByRole('button', { name: 'Lock', exact: true }).click()
  await expect(page.getByLabel('Admin key')).toBeVisible()
})

test('switching views keeps the conversation', async ({ page }) => {
  await page.route('**/api/admin/**', (r) => r.fulfill({ status: 404, json: { detail: 'Not found' } }))
  await page.goto('/')
  await page.getByRole('button', { name: /Prompt injection/ }).click()
  await expect(page.getByTestId('verdict').last()).toHaveText('Blocked', { timeout: 30_000 })
  await page.getByRole('button', { name: 'Audit', exact: true }).click()
  await expect(page.locator('.dossier')).toBeHidden()
  await page.getByRole('button', { name: 'Desk', exact: true }).click()
  await expect(page.getByTestId('verdict').last()).toHaveText('Blocked')
})
