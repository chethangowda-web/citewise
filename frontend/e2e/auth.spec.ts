import { expect, test, type Page } from '@playwright/test'

// Needs a backend started with AUTH_ENABLED=true (and a JWT_SECRET):  E2E_AUTH=1 npx playwright test auth
test.skip(!process.env.E2E_AUTH, 'set E2E_AUTH=1 against a backend with AUTH_ENABLED=true')

const PASSWORD = 'correct-horse-battery'
const stamp = Date.now()
const alice = `alice${stamp}@example.com`
const bob = `bob${stamp}@example.com`

const signUp = async (page: Page, email: string) => {
  await page.getByRole('button', { name: /Create an account/ }).click()
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Create account' }).click()
  await expect(page.locator('.account')).toContainText(email)
}
const signOut = async (page: Page) => {
  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page.getByRole('form', { name: 'Sign in' })).toBeVisible()
}
const ask = async (page: Page, q: string) => {
  await page.getByLabel('Your question').fill(q)
  await page.getByRole('button', { name: /^Ask/ }).click()
}

test('the app is locked until you sign in, and private uploads and history stay private', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('form', { name: 'Sign in' })).toBeVisible()
  await expect(page.getByLabel('Your question')).toHaveCount(0) // nothing of the app is reachable

  // a wrong password says so, and is not mistaken for an expired session
  await page.getByLabel('Email').fill(alice)
  await page.getByLabel('Password').fill('not-the-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('alert')).toContainText('Incorrect email or password')

  await signUp(page, alice)
  const secret = `Secret Plan ${stamp}\n\nThe launch codename for project ${stamp} is Nightingale Falcon.\n`
  await page.getByTestId('file-input').setInputFiles({
    name: `secret_plan_${stamp}.md`,
    mimeType: 'text/markdown',
    buffer: Buffer.from(secret),
  })
  const folder = page.locator('.folder', { hasText: 'Secret Plan' })
  await expect(folder).toContainText('private', { timeout: 60_000 })

  await ask(page, `What is the launch codename for project ${stamp}?`)
  await expect(page.getByTestId('answer').last()).toContainText('Nightingale', { timeout: 60_000 })
  await expect(page.locator('.history-item')).toHaveCount(1)

  // a second user sees none of it
  await signOut(page)
  await signUp(page, bob)
  await expect(page.locator('.folder', { hasText: 'Secret Plan' })).toHaveCount(0)
  await expect(page.locator('.history-item')).toHaveCount(0)
  await ask(page, `What is the launch codename for project ${stamp}?`)
  await expect(page.getByTestId('answer').last()).not.toContainText('Nightingale', { timeout: 60_000 })
  await expect(page.getByTestId('verdict').last()).toHaveText('Not on file')

  // and the first user gets everything back after signing in again
  await signOut(page)
  await page.getByLabel('Email').fill(alice)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.locator('.folder', { hasText: 'Secret Plan' })).toBeVisible()
  await expect(page.locator('.history-item')).toHaveCount(1)
})

test('a session that has expired sends you back to sign in', async ({ page }) => {
  await page.goto('/')
  await signUp(page, `carol${stamp}@example.com`)
  await page.evaluate(() => localStorage.setItem('policyDeskToken', 'garbage'))
  await page.reload()
  await expect(page.getByRole('form', { name: 'Sign in' })).toBeVisible()
})
