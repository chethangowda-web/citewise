import react from '@vitejs/plugin-react'
import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'

// VITE_API_PROXY points the dev and preview servers at a different backend (default: the local one).
export default defineConfig(({ mode }) => {
  const target = loadEnv(mode, '.', '')['VITE_API_PROXY'] || 'http://localhost:8000'
  return {
    plugins: [react()],
    server: { port: 5173, proxy: { '/api': target } },
    preview: { proxy: { '/api': target } },
    test: { environment: 'node', include: ['src/**/*.test.ts'] },
  }
})
