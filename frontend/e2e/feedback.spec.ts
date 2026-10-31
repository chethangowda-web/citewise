import { readFileSync } from 'node:fs'
import { expect, test, type Browser, type Page } from '@playwright/test'

// Real model and embeddings. Needs a backend with AUTH_ENABLED=true and the admin key:
//   E2E_AUTH=1 E2E_ADMIN_KEY=<ADMIN_API_KEY> npx playwright test feedback
test.skip(!process.env.E2E_AUTH || !process.env.E2E_ADMIN_KEY, 'set E2E_AUTH=1 and E2E_ADMIN_KEY')

const KEY = process.env.E2E_ADMIN_KEY ?? ''
const stamp = Date.now()
const email = (n: string) => `${n}${stamp}@example.com`
const PW = 'correct-horse-battery'
const OFF_TOPIC = 'What is the capital of France?'

test.afterAll(async ({ request }) => {
  const headers = { 'X-Admin-Key': KEY }
  const users = (await (await request.get('/api/admin/users', { headers })).json()) as { id: number; email: string }[]
  for (const u of users.filter((x) => x.email.endsWith(`${stamp}@example.com`))) await request.delete(`/api/admin/users/${u.id}`, { headers })
})

const signIn = async (page: Page, who: string) => {
  await page.goto('/')
  await page.getByLabel('Email').fill(email(who))
  await page.getByLabel('Password').fill(PW)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.locator('.account')).toContainText(email(who))
}
const ask = async (page: Page, q: string) => {
  await page.getByLabel('Your question').fill(q)
  await page.getByRole('button', { name: /^Ask/ }).click()
}
const adminTab = async (page: Page, tab: string) => {
  await page.getByRole('navigation', { name: 'View' }).getByRole('button', { name: 'Admin' }).click()
  await page.getByRole('button', { name: new RegExp(`^${tab}`) }).click()
}
const newContext = async (browser: Browser) => (await browser.newContext({ baseURL: process.env.E2E_BASE_URL })).newPage()

test('feedback, asking a person, the staff inbox, exports, and erasing your own account', async ({ page, browser, request }) => {
  const boss = await (await request.post('/api/auth/register', { data: { email: email('boss'), password: PW } })).json()
  await request.patch(`/api/admin/users/${boss.user.id}`, { headers: { 'X-Admin-Key': KEY }, data: { role: 'admin' } })

  // Alice signs up and asks something the archive cannot answer
  await page.goto('/')
  await page.getByRole('button', { name: /Create an account/ }).click()
  await page.getByLabel('Email').fill(email('alice'))
  await page.getByLabel('Password').fill(PW)
  await page.getByRole('button', { name: 'Create account' }).click()
  await expect(page.locator('.account')).toContainText(email('alice'))

  await ask(page, OFF_TOPIC)
  await expect(page.getByTestId('verdict').last()).toHaveText('Not on file', { timeout: 60_000 })
  await page.getByRole('button', { name: 'Not helpful' }).click()
  await expect(page.getByTestId('reasons')).toBeVisible()
  await page.getByTestId('reasons').getByRole('button', { name: 'Out of date' }).click()
  await expect(page.getByText('Thank you. That helps us fix it.')).toBeVisible()

  await page.getByRole('button', { name: 'Ask a person' }).click()
  await page.getByLabel('Add a note for the team (optional)').fill('Needed for a customer trip next week')
  await page.getByRole('button', { name: 'Send to the team' }).click()
  await expect(page.getByTestId('escalated')).toBeVisible()

  // an answered question can be praised, and taking the vote back works
  await page.getByTestId('new-chat').click()
  await ask(page, 'How many days do I have to request a refund?')
  await expect(page.getByTestId('verdict').last()).toHaveText('Verified', { timeout: 60_000 })
  await expect(page.getByRole('button', { name: 'Ask a person' })).toHaveCount(0) // only offered when it did not help
  await page.getByRole('button', { name: 'Helpful', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Helpful', exact: true })).toHaveAttribute('aria-pressed', 'true')

  await page.getByRole('button', { name: 'Account' }).click()
  await expect(page.getByTestId('asked')).toContainText(OFF_TOPIC)
  await expect(page.getByTestId('asked')).toContainText('waiting')

  // the admin sees the unhelpful answer and answers the question
  const adminPage = await newContext(browser)
  await signIn(adminPage, 'boss')
  await adminTab(adminPage, 'feedback')
  const review = adminPage.getByTestId('admin-feedback')
  await expect(review).toContainText(OFF_TOPIC)
  await expect(review).toContainText('Out of date')
  await adminPage.getByRole('button', { name: /^inbox/ }).click()
  const inbox = adminPage.getByTestId('admin-inbox')
  const card = inbox.locator('article', { hasText: email('alice') }) // other people's questions may be waiting too
  await expect(card).toContainText(OFF_TOPIC)
  await expect(card).toContainText('Needed for a customer trip next week')
  await card.getByRole('textbox').fill('Paris. This is a policy archive, so we only know company rules.')
  await card.getByRole('button', { name: 'Send answer' }).click()
  await expect(inbox.locator('article', { hasText: email('alice') })).toHaveCount(0) // answered, so out of the open inbox

  // the CSV export downloads, with the ledger inside
  await adminPage.getByRole('navigation', { name: 'View' }).getByRole('button', { name: 'Audit' }).click()
  const [csv] = await Promise.all([adminPage.waitForEvent('download'), adminPage.getByRole('button', { name: 'Export CSV' }).click()])
  expect(csv.suggestedFilename()).toMatch(/^citewise-audit-\d{8}\.csv$/)
  const csvText = readFileSync((await csv.path()) as string, 'utf8')
  expect(csvText.split('\n')[0]).toContain('outcome')
  expect(csvText).toContain(OFF_TOPIC)

  // Alice sees the reply, downloads her data, then erases her account
  await page.reload()
  await page.getByRole('button', { name: 'Account' }).click()
  await expect(page.getByTestId('asked')).toContainText('answered')
  await expect(page.getByTestId('asked')).toContainText('Paris')
  const [mine] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'Download my data' }).click()])
  const data = JSON.parse(readFileSync((await mine.path()) as string, 'utf8'))
  expect(data.account.email).toBe(email('alice'))
  expect(data.conversations.length).toBe(2)
  expect(data.questions_sent_to_staff[0].reply).toContain('Paris')

  await page.getByRole('button', { name: 'Delete my account…' }).click()
  await page.getByLabel('Confirm with your password').fill('definitely-not-it')
  await page.getByTestId('confirm-erase').click()
  await expect(page.getByRole('alert')).toContainText('That password is not right')
  await page.getByLabel('Confirm with your password').fill(PW)
  await page.getByTestId('confirm-erase').click()
  await expect(page.getByRole('form', { name: 'Sign in' })).toBeVisible()

  // she is really gone: her answers vanished from the review queue and she can no longer sign in
  await adminTab(adminPage, 'feedback')
  await expect(adminPage.getByTestId('admin-feedback')).not.toContainText(OFF_TOPIC)
  await page.getByLabel('Email').fill(email('alice'))
  await page.getByLabel('Password').fill(PW)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('alert')).toContainText('Incorrect email or password')
  await adminPage.context().close()
})
