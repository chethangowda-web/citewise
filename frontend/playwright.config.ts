import { defineConfig } from '@playwright/test'

// Uses the locally installed Chrome (no browser download).
//   dev:        npx playwright test                       (starts Vite, needs the backend on :8000)
//   production: E2E_BASE_URL=http://localhost:8080 npx playwright test   (tests the nginx build)
const baseURL = process.env.E2E_BASE_URL ?? 'http://localhost:5173'

export default defineConfig({
  testDir: './e2e',
  timeout: 90_000,
  workers: 1,
  reporter: [['list']],
  use: { baseURL, channel: 'chrome', screenshot: 'only-on-failure' },
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : { command: 'npm run dev', url: 'http://localhost:5173', reuseExistingServer: true, timeout: 60_000 },
})
