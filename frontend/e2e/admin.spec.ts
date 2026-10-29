import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

// Needs a backend with AUTH_ENABLED=true and the admin key:
//   E2E_AUTH=1 E2E_ADMIN_KEY=<ADMIN_API_KEY> npx playwright test admin
test.skip(!process.env.E2E_AUTH || !process.env.E2E_ADMIN_KEY, 'set E2E_AUTH=1 and E2E_ADMIN_KEY')

const KEY = process.env.E2E_ADMIN_KEY ?? ''
const PASSWORD = 'correct-horse-battery'
const stamp = Date.now()
const email = (n: string) => `${n}${stamp}@example.com`
const TEAM = `QA${stamp}`
const adminHeaders = { 'X-Admin-Key': KEY }

const register = async (request: APIRequestContext, who: string) => {
  const r = await request.post('/api/auth/register', { data: { email: email(who), password: PASSWORD } })
  expect(r.ok()).toBeTruthy()
  return (await r.json()) as { token: string; user: { id: number } }
}
const signIn = async (page: Page, who: string) => {
  await page.goto('/')
  await page.getByLabel('Email').fill(email(who))
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.locator('.account')).toContainText(email(who))
}
const signOut = async (page: Page) => {
  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page.getByRole('form', { name: 'Sign in' })).toBeVisible()
}

test.afterAll(async ({ request }) => {
  // leave the real database as we found it
  const users = (await (await request.get('/api/admin/users', { headers: adminHeaders })).json()) as { id: number; email: string }[]
  for (const u of users.filter((x) => x.email.endsWith(`${stamp}@example.com`)))
    await request.delete(`/api/admin/users/${u.id}`, { headers: adminHeaders })
  // a team that still has documents cannot be deleted, so remove its documents first
  const docs = (await (await request.get('/api/admin/documents', { headers: adminHeaders })).json()) as { id: number; team: string | null }[]
  for (const d of docs.filter((x) => x.team === TEAM)) await request.delete(`/api/documents/${d.id}`, { headers: adminHeaders })
  const teams = (await (await request.get('/api/admin/teams', { headers: adminHeaders })).json()) as { id: number; name: string }[]
  for (const t of teams.filter((x) => x.name === TEAM)) await request.delete(`/api/admin/teams/${t.id}`, { headers: adminHeaders })
})

test('an admin manages teams and roles; team documents reach members only; viewers cannot upload', async ({ page, request }) => {
  const boss = await register(request, 'boss')
  const eve = await register(request, 'eve') // will join the team
  const frank = await register(request, 'frank') // outsider, later made a viewer
  // the very first account is the admin; the real database already has one, so promote boss with the admin key
  const promote = await request.patch(`/api/admin/users/${boss.user.id}`, { headers: adminHeaders, data: { role: 'admin' } })
  expect(promote.ok()).toBeTruthy()

  await signIn(page, 'boss')
  await page.getByRole('navigation', { name: 'View' }).getByRole('button', { name: 'Admin' }).click()
  await expect(page.getByTestId('admin-users')).toContainText(email('eve'))

  // roles: make frank a viewer from the users table
  await page.getByLabel(`Role of ${email('frank')}`).selectOption('viewer')
  await expect(page.getByLabel(`Role of ${email('frank')}`)).toHaveValue('viewer')

  // teams: create one and put eve in it
  await page.getByRole('button', { name: 'teams' }).click()
  await page.getByLabel('New team name').fill(TEAM)
  await page.getByRole('button', { name: 'Create team' }).click()
  const card = page.locator('.team-card', { hasText: TEAM })
  await expect(card).toBeVisible()
  await card.getByLabel(`Add a member to ${TEAM}`).selectOption({ label: email('eve') })
  await expect(card).toContainText(email('eve'))

  // eve uploads a team document (the API enforces membership; the UI picker is exercised below)
  const teams = (await (await request.get('/api/admin/teams', { headers: adminHeaders })).json()) as { id: number; name: string }[]
  const teamId = teams.find((t) => t.name === TEAM)!.id
  const up = await request.post('/api/documents', {
    headers: { Authorization: `Bearer ${eve.token}` },
    multipart: {
      scope: 'team',
      team_id: String(teamId),
      file: { name: `qa_notes_${stamp}.md`, mimeType: 'text/markdown', buffer: Buffer.from(`# QA Notes\n\nThe release codename for build ${stamp} is Copper Heron.\n`) },
    },
  })
  expect(up.ok()).toBeTruthy()

  // the admin console sees it, and says who can see it
  await page.getByRole('button', { name: 'documents' }).click()
  await expect(page.getByTestId('admin-docs')).toContainText(`Team ${TEAM}`)
  await signOut(page)

  // eve (member): sees it labelled with the team, can ask about it, and the upload picker offers her team
  await signIn(page, 'eve')
  await expect(page.locator('.folder', { hasText: `Qa Notes ${stamp}` })).toContainText(`team ${TEAM}`)
  await expect(page.getByTestId('scope-select').locator('option', { hasText: `Team ${TEAM}` })).toHaveCount(1)
  await page.getByLabel('Your question').fill('What is the QA team release codename?')
  await page.getByRole('button', { name: /^Ask/ }).click()
  await expect(page.getByTestId('answer').last()).toContainText('Copper Heron', { timeout: 60_000 })
  await expect(page.getByRole('navigation', { name: 'View' }).getByRole('button', { name: 'Admin' })).toHaveCount(0)
  await signOut(page)

  // frank (viewer, not in the team): sees nothing of it, gets a refusal, and cannot upload
  await signIn(page, 'frank')
  await expect(page.locator('.folder', { hasText: `Qa Notes ${stamp}` })).toHaveCount(0)
  await expect(page.getByText('Your role can read and ask, but not upload.')).toBeVisible()
  await page.getByLabel('Your question').fill('What is the QA team release codename?')
  await page.getByRole('button', { name: /^Ask/ }).click()
  await expect(page.getByTestId('verdict').last()).toHaveText('Not on file', { timeout: 60_000 })
  await expect(page.getByTestId('answer').last()).not.toContainText('Copper Heron')
  const denied = await request.post('/api/documents', {
    headers: { Authorization: `Bearer ${frank.token}` },
    multipart: { file: { name: 'x.md', mimeType: 'text/markdown', buffer: Buffer.from('# X\n\nhello there') } },
  })
  expect(denied.status()).toBe(403)
  await signOut(page)

  // the admin removes the document from the console
  await signIn(page, 'boss')
  await page.getByRole('navigation', { name: 'View' }).getByRole('button', { name: 'Admin' }).click()
  await page.getByRole('button', { name: 'documents' }).click()
  page.once('dialog', (d) => void d.accept())
  await page.getByRole('button', { name: new RegExp(`Delete Qa Notes ${stamp}`) }).click()
  await expect(page.getByTestId('admin-docs')).not.toContainText(`Qa Notes ${stamp}`)
})
