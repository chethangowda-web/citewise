import { expect, test, type Page } from '@playwright/test'

const ask = async (page: Page, q: string) => {
  await page.getByLabel('Your question').fill(q)
  await page.getByRole('button', { name: /^Ask/ }).click()
}

test('a conversation is saved, survives a reload, reopens with its evidence, and can be deleted', async ({ page }) => {
  await page.goto('/')
  const tag = `e2e${Date.now()}`
  const question = `How many days do I have to request a refund? ${tag}`

  await ask(page, question)
  await expect(page.getByTestId('verdict').last()).toHaveText('Verified', { timeout: 60_000 })
  const item = page.locator('.history-item', { hasText: tag })
  await expect(item).toHaveCount(1)
  await expect(item).toHaveClass(/on/) // the new conversation is the active one

  // a follow-up lands in the same conversation instead of starting another
  await ask(page, `Is the refund a full refund? ${tag}`)
  await expect(page.getByTestId('answer-sheet')).toHaveCount(2, { timeout: 60_000 })
  await expect(page.getByTestId('verdict')).toHaveCount(2, { timeout: 60_000 })
  await expect(page.locator('.history-item', { hasText: tag })).toHaveCount(1)

  await page.reload()
  await expect(page.getByText('Ask the archive.')).toBeVisible() // a reload starts blank...
  await page.locator('.history-open', { hasText: tag }).click() // ...and the saved conversation comes back
  await expect(page.getByTestId('question')).toHaveCount(2)
  await expect(page.getByTestId('verdict').first()).toHaveText('Verified')
  await expect(page.locator('.slip').first()).toBeVisible() // sources were restored, not just the text
  await expect(page.getByTestId('answer').first()).toContainText('30')

  await page.getByTestId('new-chat').click()
  await expect(page.getByText('Ask the archive.')).toBeVisible()
  await expect(page.getByTestId('answer-sheet')).toHaveCount(0)

  page.once('dialog', (d) => void d.accept())
  await page.locator('.history-item', { hasText: tag }).getByRole('button', { name: /Delete conversation/ }).click()
  await expect(page.locator('.history-item', { hasText: tag })).toHaveCount(0)
})

test('the trace follows the real pipeline stage while a question is running', async ({ page }) => {
  await page.goto('/')
  await ask(page, 'What is the hotel limit when I travel to London?')
  await expect(page.locator('.station.st-active, .station.st-done').first()).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId('verdict').last()).toBeVisible({ timeout: 60_000 })
  await expect(page.locator('.station.st-pass')).toHaveCount(4)
})
