import { act, cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import App from './App'

const users = { items: [{ id: 'rina-id', name: 'Rina Shah', email: 'rina@example.test' }] }
const result = {
  items: [{
    external_id: 'ATLAS-231', title: 'Investigate pool timeout', source: 'jira',
    matched_field: 'comment', excerpt: 'The request pool timed out.', author: 'Maya',
    matched_author: 'Rina', container: 'Atlas', updated_at: '2026-08-20T00:00:00Z',
    url: 'https://broken.example/ATLAS-231', score: 1,
  }],
  facets: { jira: 1, confluence: 0, slack: 0, github: 0 },
  search_id: 'search-123', profile: 'released',
}
const json = (body: unknown, init?: ResponseInit) =>
  Promise.resolve(new Response(JSON.stringify(body), init))

beforeEach(() => {
  const storage = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
  })
  vi.stubGlobal('crypto', { randomUUID: () => 'browser-session' })
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/api/demo-users')) return json(users)
    if (url.endsWith('/api/answer')) return json({
      answer: 'The pool is saturated.',
      citations: [
        { external_id: 'ATLAS-231', title: 'Pool timeout', url: 'https://same.example/ATLAS-231' },
        { external_id: 'ATLAS-231', title: 'Pool timeout', url: 'https://same.example/ATLAS-231' },
      ],
      follow_ups: [],
    })
    if (url.endsWith('/api/documents/jira/ATLAS-231')) return json({
      source: 'jira', external_id: 'ATLAS-231', kind: 'issue', title: 'Investigate pool timeout',
      author: 'Maya', container: 'Atlas', payload: { status: 'Open', description: 'Pool exhausted.' },
    })
    return json(result)
  }))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function searchFor(query = 'pool timeout') {
  render(<App />)
  await screen.findByText(/Demo identity only/)
  await userEvent.type(screen.getByRole('searchbox'), query)
  await userEvent.keyboard('{Enter}')
}

it('shows no empty state while search is still loading', async () => {
  let finishSearch!: (response: Response) => void
  vi.mocked(fetch).mockImplementation((input) => {
    const url = String(input)
    if (url.endsWith('/api/demo-users')) return json(users)
    if (url.includes('/api/search?')) return new Promise((resolve) => { finishSearch = resolve })
    return json({ answer: 'No evidence.', citations: [], follow_ups: [] })
  })
  await searchFor('unknown project')

  expect(screen.getByRole('status')).toHaveTextContent('Searching company knowledge')
  expect(screen.queryByText('No results found')).not.toBeInTheDocument()

  await act(async () => finishSearch(new Response(JSON.stringify({
    items: [], facets: { jira: 0, confluence: 0, slack: 0, github: 0 },
    search_id: 'empty', profile: 'released',
  }))))
  expect(await screen.findByText('No results found')).toBeVisible()
})

it('shows the answer and only one provenance for one document', async () => {
  await searchFor()

  expect(await screen.findByText('The pool is saturated.')).toBeVisible()
  expect(screen.getAllByRole('button', { name: /Pool timeout/ })).toHaveLength(1)
})

it('opens a local document panel, records the click, and returns focus on close', async () => {
  await searchFor()
  const resultButton = await screen.findByRole('button', { name: 'Investigate pool timeout' })
  await userEvent.click(resultButton)

  expect(await screen.findByRole('dialog')).toBeVisible()
  expect(await screen.findByText('Pool exhausted.')).toBeVisible()
  expect(fetch).toHaveBeenCalledWith(
    '/api/search-events/search-123/click',
    expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ source: 'jira', external_id: 'ATLAS-231', rank: 1 }),
    }),
  )
  await userEvent.keyboard('{Escape}')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(resultButton).toHaveFocus()
})

it('does not show unfinished filter or history controls', async () => {
  render(<App />)
  expect(await screen.findByText(/Demo identity only/)).toBeVisible()

  for (const name of ['Anytime', 'Who from', 'What type', 'My history']) {
    expect(screen.queryByRole('button', { name })).not.toBeInTheDocument()
  }
})
