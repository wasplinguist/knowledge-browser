import { act, cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'

import DocumentPanel from './DocumentPanel'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const selection = { source: 'jira' as const, external_id: 'ATLAS-231', title: 'Pool timeout' }

it('shows loading, Jira content, and a safe error', async () => {
  let finish!: (response: Response) => void
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => { finish = resolve })))
  const view = render(<DocumentPanel item={selection} userId="rina-id" onClose={vi.fn()} />)

  expect(screen.getByRole('status')).toHaveTextContent('Loading document')
  expect(screen.getByRole('button', { name: 'Close document' })).toHaveFocus()

  await act(async () => finish(new Response(JSON.stringify({
    ...selection, kind: 'issue', payload: { status: 'Open', description: 'Pool exhausted.' },
  }))))
  expect(await screen.findByText('Pool exhausted.')).toBeVisible()
  view.unmount()

  vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('offline'))))
  render(<DocumentPanel item={selection} userId="rina-id" onClose={vi.fn()} />)
  expect(await screen.findByRole('alert')).toHaveTextContent('This document is unavailable.')
})

it.each([
  ['confluence', { space: 'ENG', sections: [{ heading: 'Root cause', body: 'Bounded retries.' }] }, 'Root cause'],
  ['slack', { channel: '#incidents', messages: [{ author: 'Priya', text: 'Relay alert' }] }, 'Relay alert'],
  ['github', { repository: 'northstar/browser', number: 81, record_type: 'pull request', body: 'Bound retry fan-out.' }, 'pull request'],
] as const)('renders %s source content', async (source, payload, expected) => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify({
    source, external_id: 'item-1', kind: 'item', title: 'Source item', payload,
  })))))

  render(<DocumentPanel item={{ source, external_id: 'item-1', title: 'Source item' }} userId="rina-id" onClose={vi.fn()} />)

  expect(await screen.findByText(expected)).toBeVisible()
})

it('ignores an older response after the selected document changes', async () => {
  const pending: Array<(response: Response) => void> = []
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => pending.push(resolve))))
  const view = render(<DocumentPanel item={{ ...selection, external_id: 'old' }} userId="rina-id" onClose={vi.fn()} />)
  view.rerender(<DocumentPanel item={{ ...selection, external_id: 'new', title: 'New' }} userId="rina-id" onClose={vi.fn()} />)

  await act(async () => pending[1](new Response(JSON.stringify({
    ...selection, external_id: 'new', title: 'New', kind: 'issue', payload: { description: 'New content' },
  }))))
  expect(await screen.findByText('New content')).toBeVisible()

  await act(async () => pending[0](new Response(JSON.stringify({
    ...selection, external_id: 'old', title: 'Old', kind: 'issue', payload: { description: 'Old content' },
  }))))
  expect(screen.queryByText('Old content')).not.toBeInTheDocument()
})

it('closes from the backdrop', async () => {
  vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})))
  const onClose = vi.fn()
  render(<DocumentPanel item={selection} userId="rina-id" onClose={onClose} />)

  await userEvent.click(screen.getByTestId('document-backdrop'))
  expect(onClose).toHaveBeenCalledOnce()
})
