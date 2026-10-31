import { execSync } from 'node:child_process'
import { expect, test, type Browser, type Page } from '@playwright/test'

// Needs a backend with AUTH_ENABLED=true; the reset-link test also reads the backend's Docker log
// (no SMTP is configured, so the link is logged). Raise AUTH_RATE_LIMIT_PER_MINUTE for the run: this file signs in a lot.
//   E2E_AUTH=1 E2E_ADMIN_KEY=<ADMIN_API_KEY> npx playwright test security
test.skip(!process.env.E2E_AUTH, 'set E2E_AUTH=1 against a backend with AUTH_ENABLED=true')

const KEY = process.env.E2E_ADMIN_KEY ?? ''
const stamp = Date.now()
const email = (n: string) => `${n}${stamp}@example.com`
const PW = 'correct-horse-battery'
const NEW_PW = 'a-brand-new-password'

test.afterAll(async ({ request }) => {
  if (!KEY) return
  const headers = { 'X-Admin-Key': KEY }
  const users = (await (await request.get('/api/admin/users', { headers })).json()) as { id: number; email: string }[]
  for (const u of users.filter((x) => x.email.endsWith(`${stamp}@example.com`))) await request.delete(`/api/admin/users/${u.id}`, { headers })
})

const signUp = async (page: Page, who: string) => {
  await page.goto('/')
  await page.getByRole('button', { name: /Create an account/ }).click()
  await page.getByLabel('Email').fill(email(who))
  await page.getByLabel('Password').fill(PW)
  await page.getByRole('button', { name: 'Create account' }).click()
  await expect(page.locator('.account')).toContainText(email(who))
}
const signIn = async (page: Page, who: string, password = PW) => {
  await page.goto('/')
  await page.getByLabel('Email').fill(email(who))
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
}
const secondDevice = async (browser: Browser, who: string) => {
  const ctx = await browser.newContext({ baseURL: process.env.E2E_BASE_URL })
  const page = await ctx.newPage()
  await signIn(page, who)
  await expect(page.locator('.account')).toContainText(email(who))
  return page
}

test('the session is an HttpOnly cookie: nothing in localStorage, nothing a script can read', async ({ page }) => {
  await signUp(page, 'cookie')
  expect(await page.evaluate(() => JSON.stringify({ ...localStorage }))).not.toMatch(/eyJ/) // no JWT stored by the page
  expect(await page.evaluate(() => document.cookie)).not.toContain('citewise_session')
  const cookie = (await page.context().cookies()).find((c) => c.name === 'citewise_session')
  expect(cookie?.httpOnly).toBe(true)
  expect(cookie?.sameSite).toBe('Lax')

  await page.reload()
  await expect(page.locator('.account')).toContainText(email('cookie')) // the cookie alone keeps you signed in

  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page.getByRole('form', { name: 'Sign in' })).toBeVisible()
  expect((await page.context().cookies()).find((c) => c.name === 'citewise_session')).toBeUndefined()
  await page.reload()
  await expect(page.getByRole('form', { name: 'Sign in' })).toBeVisible() // and signing out really ended it
})

test('a forgotten password is reset through the emailed link, once', async ({ page }) => {
  let docker = true
  try {
    execSync('docker logs --tail 1 policy-rag-backend-1', { stdio: 'ignore' })
  } catch {
    docker = false
  }
  test.skip(!docker, 'needs the policy-rag-backend-1 container to read the logged reset link')

  await signUp(page, 'forgot')
  await page.getByRole('button', { name: 'Sign out' }).click()
  await page.getByRole('button', { name: 'Forgot your password?' }).click()
  await page.getByLabel('Email').fill(email('forgot'))
  await page.getByRole('button', { name: 'Send reset link' }).click()
  await expect(page.getByTestId('forgot-sent')).toContainText('If an account exists')

  // no SMTP is configured, so the message (and its link) is in the backend log
  let token = ''
  for (let i = 0; i < 20 && !token; i++) {
    const logs = execSync('docker logs --tail 200 policy-rag-backend-1 2>&1', { encoding: 'utf8' })
    const line = logs.split('\n').reverse().find((l) => l.includes(email('forgot')) && l.includes('reset='))
    token = line?.match(/reset=([\w-]+)/)?.[1] ?? ''
    if (!token) await page.waitForTimeout(500)
  }
  expect(token.length).toBeGreaterThan(20)

  await page.goto(`/?reset=${token}`)
  await page.getByLabel('New password', { exact: true }).fill(NEW_PW)
  await page.getByLabel('Repeat it').fill(NEW_PW)
  await page.getByRole('button', { name: 'Change password' }).click()
  await expect(page.getByTestId('reset-done')).toBeVisible()
  await page.getByRole('button', { name: 'Go to sign in' }).click()
  expect(page.url()).not.toContain('reset=')

  await signIn(page, 'forgot', PW) // the old password no longer works
  await expect(page.getByRole('alert')).toContainText('Incorrect email or password')
  await page.getByLabel('Password').fill(NEW_PW)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.locator('.account')).toContainText(email('forgot'))

  await page.goto(`/?reset=${token}`) // the same link cannot be used twice
  await page.getByLabel('New password', { exact: true }).fill('yet-another-password')
  await page.getByLabel('Repeat it').fill('yet-another-password')
  await page.getByRole('button', { name: 'Change password' }).click()
  await expect(page.getByRole('alert')).toContainText('invalid or has expired')
})

test('"sign out everywhere" ends the session on every other device', async ({ page, browser }) => {
  await signUp(page, 'everywhere')
  const other = await secondDevice(browser, 'everywhere')

  await page.getByRole('button', { name: 'Account' }).click()
  await page.getByTestId('logout-all').click()
  await expect(page.getByRole('form', { name: 'Sign in' })).toBeVisible()

  await other.reload()
  await expect(other.getByRole('form', { name: 'Sign in' })).toBeVisible() // the other device lost its session too
  await other.context().close()
})

test('changing the password signs other devices out and keeps this one signed in', async ({ page, browser }) => {
  await signUp(page, 'change')
  const other = await secondDevice(browser, 'change')

  await page.getByRole('button', { name: 'Account' }).click()
  await page.getByLabel('Current password').fill('not-my-password')
  await page.getByLabel('New password').fill(NEW_PW)
  await page.getByRole('button', { name: 'Change password' }).last().click()
  await expect(page.getByRole('alert').last()).toContainText('current password is not right')

  await page.getByLabel('Current password').fill(PW)
  await page.getByRole('button', { name: 'Change password' }).last().click()
  await expect(page.getByTestId('account-message')).toContainText('Password changed')

  await page.reload()
  await expect(page.locator('.account')).toContainText(email('change')) // this device carries on
  await other.reload()
  await expect(other.getByRole('form', { name: 'Sign in' })).toBeVisible() // the other one is out
  await other.context().close()
})
