import { expect, test, type Page } from '@playwright/test'

const ask = async (page: Page, q: string) => {
  await page.getByLabel('Your question').fill(q)
  await page.getByRole('button', { name: /^Ask/ }).click()
}
const sheet = (page: Page) => page.getByTestId('answer-sheet').last()
const verdict = (page: Page) => page.getByTestId('verdict').last()

test.beforeEach(async ({ page }) => {
  await page.goto('/')
})

test('loads the archive and shows it online', async ({ page }) => {
  await expect(page.getByRole('status').filter({ hasText: 'archive online' })).toBeVisible()
  await expect(page.locator('.folder')).toHaveCount(7)
  await expect(page.getByText('Ask the archive.')).toBeInViewport() // not scrolled past on load
  await page.waitForTimeout(1500) // let entrance animations settle
  await page.screenshot({ path: 'shots/01-home.png' })
})

test('answers a real question with citations, evidence and a clean trace', async ({ page }) => {
  await ask(page, 'How many days do I have to request a refund?')
  await expect(verdict(page)).toHaveText('Verified', { timeout: 60_000 })
  await expect(page.getByTestId('answer').last()).toContainText('30')

  const cite = page.locator('.cite').first()
  await expect(cite).toBeVisible()
  await cite.hover()
  await expect(page.locator('.slip-lit')).toHaveCount(1) // hovering a citation lights its evidence slip
  await cite.click()
  await expect(page.locator('.slip blockquote.open')).toContainText('30 days') // click expands the passage

  await expect(page.locator('.station.st-pass')).toHaveCount(4)
  await page.waitForTimeout(1500) // let entrance animations settle
  await page.screenshot({ path: 'shots/02-answered.png' })
})

test('blocks prompt injection and skips later stages', async ({ page }) => {
  await page.getByRole('button', { name: /Prompt injection/ }).click()
  await expect(verdict(page)).toHaveText('Blocked', { timeout: 30_000 })
  await expect(page.locator('.station.st-fail')).toHaveCount(1)
  await expect(page.locator('.station.st-skipped')).toHaveCount(3)
  await expect(page.locator('.slip')).toHaveCount(0)
  await page.waitForTimeout(1500) // let entrance animations settle
  await page.screenshot({ path: 'shots/03-blocked.png' })
})

test('refuses off-topic questions', async ({ page }) => {
  await page.getByRole('button', { name: /Off-topic/ }).click()
  await expect(verdict(page)).toHaveText('Not on file', { timeout: 30_000 })
  await expect(sheet(page)).toHaveClass(/v-refused/)
})

test('redacts personal data in the question and warns at screening', async ({ page }) => {
  await page.getByRole('button', { name: /Leaks my data/ }).click()
  await expect(verdict(page)).toHaveText('Verified', { timeout: 60_000 })
  const redacted = page.locator('.redact')
  await expect(redacted).toHaveCount(1)
  await expect(redacted).toHaveText('123-45-6789')
  await expect(redacted).toHaveCSS('color', 'rgba(0, 0, 0, 0)') // text hidden behind the bar
  await expect(page.locator('.station.st-warn')).toHaveCount(1)
  await page.waitForTimeout(1500) // let entrance animations settle
  await page.screenshot({ path: 'shots/04-pii.png' })
})

test('composer: Enter sends, Shift+Enter does not, empty is disabled', async ({ page }) => {
  const send = page.getByRole('button', { name: /^Ask/ })
  await expect(send).toBeDisabled()
  const box = page.getByLabel('Your question')
  await box.fill('line one')
  await box.press('Shift+Enter')
  await expect(page.getByTestId('question')).toHaveCount(0)
  await box.press('Enter')
  await expect(page.getByTestId('question')).toHaveCount(1)
})

test('shows a friendly error when the API is down', async ({ page }) => {
  await page.route('**/api/chat', (r) => r.fulfill({ status: 503, json: { detail: 'The language model is temporarily unavailable.' } }))
  await ask(page, 'How many sick days do I get?')
  await expect(page.getByRole('alert')).toContainText('temporarily unavailable')
})

test('upload flow files a document and refreshes the archive', async ({ page }) => {
  let uploaded = false
  await page.route('**/api/documents', async (r) => {
    if (r.request().method() === 'POST') {
      uploaded = true
      return r.fulfill({ json: { id: 99, title: 'new', chunks: 2 } })
    }
    const real = await r.fetch()
    const docs = await real.json()
    return r.fulfill({ json: uploaded ? [...docs, { id: 99, title: 'Mocked Upload', source: 'm.md', chunks: 2 }] : docs })
  })
  await page.getByTestId('file-input').setInputFiles({ name: 'm.md', mimeType: 'text/markdown', buffer: Buffer.from('# Hi\n\nHello') })
  await expect(page.locator('.folder')).toHaveCount(8)
  await expect(page.getByText('Mocked Upload')).toBeVisible()
})

test.describe('mobile', () => {
  test.use({ viewport: { width: 390, height: 800 } })
  test('layout stacks, archive toggles, no horizontal overflow', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('.cabinet')).toBeHidden()
    await page.getByRole('button', { name: 'Archive' }).click()
    await expect(page.locator('.cabinet')).toBeVisible()
    await page.getByRole('button', { name: 'Archive' }).click()
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
    expect(overflow).toBe(false)
    await page.getByRole('button', { name: /Prompt injection/ }).click()
    await expect(verdict(page)).toHaveText('Blocked', { timeout: 30_000 })
    await page.waitForTimeout(1500) // let entrance animations settle
  await page.screenshot({ path: 'shots/05-mobile.png' })
  })
})
