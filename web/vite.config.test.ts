import { expect, it } from 'vitest'

import { createViteConfig } from './vite.config'


it('targets the API port configured for the local server', () => {
  const config = createViteConfig('43123')

  expect(config.server.proxy['/api']).toBe('http://127.0.0.1:43123')
})


it('rejects an unsafe API port before constructing the proxy URL', () => {
  expect(() => createViteConfig('8000/path')).toThrow(
    'API_PORT must be an integer between 1 and 65535',
  )
})
