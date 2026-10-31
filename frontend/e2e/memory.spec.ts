import { expect, test } from '@playwright/test'

// Real model, real embeddings. Needs a backend with AUTH_ENABLED=true (conversations belong to a user):
//   E2E_AUTH=1 E2E_ADMIN_KEY=<ADMIN_API_KEY> npx playwright test memory
test.skip(!process.env.E2E_AUTH, 'set E2E_AUTH=1 against a backend with AUTH_ENABLED=true')

const KEY = process.env.E2E_ADMIN_KEY ?? ''
const stamp = Date.now()
const email = `mem${stamp}@example.com`

test.afterAll(async ({ request }) => {
  if (!KEY) return
  const users = (await (await request.get('/api/admin/users', { headers: { 'X-Admin-Key': KEY } })).json()) as { id: number; email: string }[]
  for (const u of users.filter((x) => x.email === email)) await request.delete(`/api/admin/users/${u.id}`, { headers: { 'X-Admin-Key': KEY } })
})

test('a follow-up is understood in context, and says how it was understood', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: /Create an account/ }).click()
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill('correct-horse-battery')
  await page.getByRole('button', { name: 'Create account' }).click()
  await expect(page.locator('.account')).toContainText(email)

  const ask = async (q: string) => {
    await page.getByLabel('Your question').fill(q)
    await page.getByRole('button', { name: /^Ask/ }).click()
  }

  await ask('How many days do I have to request a refund?')
  await expect(page.getByTestId('verdict').last()).toHaveText('Verified', { timeout: 60_000 })
  await expect(page.getByTestId('interpreted')).toHaveCount(0) // nothing to remember on the first message

  // "it" means nothing on its own; with memory it is the refund
  await ask('Is it a full refund?')
  await expect(page.getByTestId('verdict')).toHaveCount(2, { timeout: 60_000 })
  const interpreted = page.getByTestId('interpreted')
  await expect(interpreted).toHaveCount(1)
  await expect(interpreted).toContainText(/refund/i)
  await expect(page.getByTestId('verdict').last()).toHaveText('Verified')
  await expect(page.getByTestId('answer').last()).not.toContainText("couldn't find this")

  // the interpretation is saved with the conversation
  await page.reload()
  await page.locator('.history-open').first().click()
  await expect(page.getByTestId('question')).toHaveCount(2)
  await expect(page.getByTestId('interpreted')).toContainText(/refund/i)
})
