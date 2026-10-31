import { expect, test, type Browser, type Page } from '@playwright/test'

// Real model. The backend must run with a tiny personal limit so one answer uses it up:
//   USER_DAILY_TOKEN_LIMIT=500   (in .env, then restart the backend)
//   E2E_AUTH=1 E2E_ADMIN_KEY=<ADMIN_API_KEY> npx playwright test usage
test.skip(!process.env.E2E_AUTH || !process.env.E2E_ADMIN_KEY, 'set E2E_AUTH=1 and E2E_ADMIN_KEY (and USER_DAILY_TOKEN_LIMIT=500 on the server)')

const KEY = process.env.E2E_ADMIN_KEY ?? ''
const stamp = Date.now()
const email = (n: string) => `${n}${stamp}@example.com`
const PW = 'correct-horse-battery'

test.afterAll(async ({ request }) => {
  const headers = { 'X-Admin-Key': KEY }
  const users = (await (await request.get('/api/admin/users', { headers })).json()) as { id: number; email: string }[]
  for (const u of users.filter((x) => x.email.endsWith(`${stamp}@example.com`))) await request.delete(`/api/admin/users/${u.id}`, { headers })
})

const ask = async (page: Page, q: string) => {
  await page.getByLabel('Your question').fill(q)
  await page.getByRole('button', { name: /^Ask/ }).click()
}
const newPage = async (browser: Browser) => (await browser.newContext({ baseURL: process.env.E2E_BASE_URL })).newPage()

test('a daily allowance stops one person, shows them their usage, and the admin sees the cost', async ({ page, browser, request }) => {
  const boss = await (await request.post('/api/auth/register', { data: { email: email('boss'), password: PW } })).json()
  await request.patch(`/api/admin/users/${boss.user.id}`, { headers: { 'X-Admin-Key': KEY }, data: { role: 'admin' } })

  await page.goto('/')
  await page.getByRole('button', { name: /Create an account/ }).click()
  await page.getByLabel('Email').fill(email('alice'))
  await page.getByLabel('Password').fill(PW)
  await page.getByRole('button', { name: 'Create account' }).click()
  await expect(page.locator('.account')).toContainText(email('alice'))

  // the first question is fine; it spends more than the 500-token allowance
  await ask(page, 'How many days do I have to request a refund?')
  await expect(page.getByTestId('verdict').last()).toHaveText('Verified', { timeout: 60_000 })

  // the second is refused up front, with a plain explanation
  await ask(page, 'How much parental leave do primary caregivers get?')
  await expect(page.getByRole('alert').filter({ hasText: 'daily allowance' })).toBeVisible({ timeout: 30_000 })

  // she can see why, and how much she has used
  await page.getByRole('button', { name: 'Account' }).click()
  const meter = page.getByTestId('usage-me')
  await expect(meter).toContainText('of 500 tokens used')
  await expect(meter.getByRole('progressbar')).toBeVisible()

  // the admin is exempt from the personal limit, and sees who spent what
  const adminPage = await newPage(browser)
  await adminPage.goto('/')
  await adminPage.getByLabel('Email').fill(email('boss'))
  await adminPage.getByLabel('Password').fill(PW)
  await adminPage.getByRole('button', { name: 'Sign in' }).click()
  await expect(adminPage.locator('.account')).toContainText(email('boss'))
  await ask(adminPage, 'How many days do I have to request a refund?')
  await expect(adminPage.getByTestId('verdict').last()).toHaveText('Verified', { timeout: 60_000 })

  await adminPage.getByRole('navigation', { name: 'View' }).getByRole('button', { name: 'Admin' }).click()
  await adminPage.getByRole('button', { name: 'usage', exact: true }).click()
  const report = adminPage.getByTestId('admin-usage')
  await expect(report).toContainText('500 tokens per person per day')
  const row = report.locator('tr', { hasText: email('alice') })
  await expect(row).toBeVisible()
  await expect(row.locator('td').nth(1)).toHaveText('1') // one answered question: the refused one cost nothing
  expect(Number((await row.locator('td').nth(2).innerText()).replace(/\D/g, ''))).toBeGreaterThan(500)
  await expect(adminPage.getByTestId('usage-cost')).toContainText('$')
  await adminPage.context().close()
})
