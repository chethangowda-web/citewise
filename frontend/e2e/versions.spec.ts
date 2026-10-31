import { expect, test } from '@playwright/test'

// Real model, real embeddings. Needs a backend with AUTH_ENABLED=true and the admin key:
//   E2E_AUTH=1 E2E_ADMIN_KEY=<ADMIN_API_KEY> npx playwright test versions
test.skip(!process.env.E2E_AUTH || !process.env.E2E_ADMIN_KEY, 'set E2E_AUTH=1 and E2E_ADMIN_KEY')

const KEY = process.env.E2E_ADMIN_KEY ?? ''
const stamp = Date.now()
const email = `ver${stamp}@example.com`
const FILE = `mentorship_program_${stamp}.md`
const body = (n: string) => Buffer.from(`# Mentorship Program\n\nEvery employee receives ${n} mentorship sessions per quarter with a senior colleague.\n`)
const QUESTION = 'How many mentorship sessions does each employee receive per quarter?'

test.afterAll(async ({ request }) => {
  const headers = { 'X-Admin-Key': KEY }
  const users = (await (await request.get('/api/admin/users', { headers })).json()) as { id: number; email: string }[]
  for (const u of users.filter((x) => x.email === email)) await request.delete(`/api/admin/users/${u.id}`, { headers })
})

test('documents keep their history, warn when old, and an admin can restore a version', async ({ page, request }) => {
  const reg = await request.post('/api/auth/register', { data: { email, password: 'correct-horse-battery' } })
  const uid = ((await reg.json()) as { user: { id: number } }).user.id
  await request.patch(`/api/admin/users/${uid}`, { headers: { 'X-Admin-Key': KEY }, data: { role: 'admin' } })

  await page.goto('/')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill('correct-horse-battery')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.locator('.account')).toContainText(email)

  const upload = async (n: string, date: string) => {
    await page.getByTestId('effective-date').fill(date)
    await page.getByTestId('file-input').setInputFiles({ name: FILE, mimeType: 'text/markdown', buffer: body(n) })
  }
  const ask = async () => {
    await page.getByLabel('Your question').fill(QUESTION)
    await page.getByRole('button', { name: /^Ask/ }).click()
  }
  const folder = page.locator('.folder', { hasText: 'Mentorship Program' })

  // version 1, dated long ago: the answer works but the evidence carries a freshness warning
  await upload('three', '2020-01-01')
  await expect(folder).toContainText('2020-01-01', { timeout: 60_000 })
  await expect(folder).toContainText('may be outdated')
  await ask()
  await expect(page.getByTestId('answer').last()).toContainText(/\b(3|three)\b/i, { timeout: 60_000 })
  await expect(page.getByTestId('stale').last()).toContainText('2020-01-01')

  // version 2 replaces it for answers, and the upload says so
  await upload('five', '2026-06-01')
  await expect(page.getByTestId('notice')).toContainText('filed as version 2', { timeout: 60_000 })
  await expect(folder).toContainText('v2')
  await expect(folder).not.toContainText('may be outdated')
  await page.getByTestId('new-chat').click()
  await ask()
  await expect(page.getByTestId('answer').last()).toContainText(/\b(5|five)\b/i, { timeout: 60_000 })
  await expect(page.getByTestId('answer').last()).not.toContainText(/\b(3|three)\b/i)
  await expect(page.getByTestId('stale')).toHaveCount(0)

  // uploading the same text again changes nothing
  await upload('five', '')
  await expect(page.getByTestId('notice')).toContainText('nothing changed', { timeout: 60_000 })

  // the admin console shows the archived version and can bring it back
  await page.getByRole('navigation', { name: 'View' }).getByRole('button', { name: 'Admin' }).click()
  await page.getByRole('button', { name: 'documents' }).click()
  const row = page.getByTestId('admin-docs').locator('tr', { hasText: 'Mentorship Program' })
  await expect(row).toHaveCount(2)
  await expect(page.getByTestId('admin-docs').locator('tr', { hasText: 'archived' })).toContainText('v1')
  await page.getByRole('button', { name: /Restore version 1/ }).click()
  await expect(page.getByTestId('admin-docs').locator('tr', { hasText: 'archived' })).toContainText('v2')

  // and answers follow the restored text
  await page.getByRole('navigation', { name: 'View' }).getByRole('button', { name: 'Desk' }).click()
  await page.getByTestId('new-chat').click()
  await ask()
  await expect(page.getByTestId('answer').last()).toContainText(/\b(3|three)\b/i, { timeout: 60_000 })
})
