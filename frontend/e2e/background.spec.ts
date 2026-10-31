import { expect, test } from '@playwright/test'

// Real model and embeddings: this files a ~250 KB document (about 300 passages) in the background.
//   E2E_AUTH=1 E2E_ADMIN_KEY=<ADMIN_API_KEY> npx playwright test background
test.skip(!process.env.E2E_AUTH || !process.env.E2E_ADMIN_KEY, 'set E2E_AUTH=1 and E2E_ADMIN_KEY')

const KEY = process.env.E2E_ADMIN_KEY ?? ''
const stamp = Date.now()
const email = `bg${stamp}@example.com`

test.afterAll(async ({ request }) => {
  const headers = { 'X-Admin-Key': KEY }
  const users = (await (await request.get('/api/admin/users', { headers })).json()) as { id: number; email: string }[]
  for (const u of users.filter((x) => x.email === email)) await request.delete(`/api/admin/users/${u.id}`, { headers })
})

// a long handbook; one fact is buried in the middle
function handbook(): Buffer {
  const lines: string[] = ['# Group Operations Handbook', '']
  for (let n = 1; n <= 2400; n++) {
    lines.push(`Section ${n}. Staff in region ${n % 40} may claim up to ${50 + (n % 90)} euros for item ${n} per quarter, with a manager's approval.`)
    if (n % 6 === 0) lines.push('')
    if (n === 1200) lines.push('', 'The emergency evacuation assembly point for the Lisbon office is the blue car park behind the main entrance.', '')
  }
  return Buffer.from(lines.join('\n'))
}

test('a big document is filed in the background while the page shows progress, then answers come from it', async ({ page }) => {
  // ~300 passages against the free tier's 100 embedding requests a minute: filing genuinely takes a few minutes
  test.setTimeout(480_000)
  await page.goto('/')
  await page.getByRole('button', { name: /Create an account/ }).click()
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill('correct-horse-battery')
  await page.getByRole('button', { name: 'Create account' }).click()
  await expect(page.locator('.account')).toContainText(email)

  const body = handbook()
  expect(body.length).toBeGreaterThan(200_000) // over the background threshold
  expect(body.length).toBeLessThan(5 * 1024 * 1024)

  const accepted = page.waitForResponse((r) => r.url().endsWith('/api/documents') && r.request().method() === 'POST')
  const polled = page.waitForRequest((r) => /\/api\/documents\/jobs\/[0-9a-f]{32}$/.test(r.url()))
  await page.getByTestId('file-input').setInputFiles({ name: 'group_operations_handbook.md', mimeType: 'text/markdown', buffer: body })

  expect((await accepted).status()).toBe(202) // taken at once, not held open while ~300 passages are embedded
  await polled // and the page then asks how it is going

  const folder = page.locator('.folder', { hasText: 'Group Operations Handbook' })
  await expect(folder).toBeVisible({ timeout: 420_000 })
  const passages = Number(((await folder.locator('.folder-meta').innerText()).match(/(\d+) passages/) ?? [])[1])
  expect(passages).toBeGreaterThan(150)
  await expect(page.getByTestId('upload-status')).toContainText('Drop .pdf') // progress text is gone once it is done

  await page.getByLabel('Your question').fill('Where is the emergency evacuation assembly point for the Lisbon office?')
  await page.getByRole('button', { name: /^Ask/ }).click()
  await expect(page.getByTestId('verdict').last()).toHaveText('Verified', { timeout: 60_000 })
  await expect(page.getByTestId('answer').last()).toContainText(/blue car park/i)
})
