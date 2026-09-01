import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export function createViteConfig(configuredApiPort = process.env.API_PORT) {
  const rawApiPort = configuredApiPort || '8000'
  const apiPort = Number(rawApiPort)
  if (
    !/^\d+$/.test(rawApiPort)
    || !Number.isInteger(apiPort)
    || apiPort < 1
    || apiPort > 65535
  ) {
    throw new Error('API_PORT must be an integer between 1 and 65535')
  }

  return {
    plugins: [react()],
    server: { proxy: { '/api': `http://127.0.0.1:${apiPort}` } },
    test: { environment: 'jsdom', setupFiles: ['./src/test-setup.ts'] },
  }
}

export default defineConfig(() => createViteConfig())
