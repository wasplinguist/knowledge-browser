import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import App from './App'

describe('App', () => {
  it('shows the product and foundation status', () => {
    render(<App />)

    expect(screen.getByRole('heading', { name: 'Knowledge Browser' })).toBeInTheDocument()
    expect(screen.getByText('Foundation ready')).toBeInTheDocument()
  })
})
