import { useEffect, useRef, useState } from 'react'

import { getDocument } from './api'
import type { DocumentDetail, DocumentSelection, PersonText } from './types'

type Props = { item: DocumentSelection; userId: string; onClose: () => void }
const text = (item: PersonText) => typeof item === 'string' ? item : item.body || item.text || ''

function JiraView({ document }: { document: DocumentDetail }) {
  const payload = document.payload
  return <div className="document-body">
    <p className="document-meta">{[payload.issue_key, payload.status, payload.priority].filter(Boolean).join(' · ')}</p>
    {payload.description && <p>{payload.description}</p>}
    {Boolean(payload.components?.length) && <p><strong>Components:</strong> {payload.components?.join(', ')}</p>}
    {Boolean(payload.affected_versions?.length) && <p><strong>Affected:</strong> {payload.affected_versions?.join(', ')}</p>}
    {Boolean(payload.fix_versions?.length) && <p><strong>Fix:</strong> {payload.fix_versions?.join(', ')}</p>}
    {payload.comments?.map((comment, index) => <article className="document-message" key={index}>
      {typeof comment !== 'string' && comment.author && <strong>{comment.author}</strong>}
      <p>{text(comment)}</p>
    </article>)}
  </div>
}

function ConfluenceView({ document }: { document: DocumentDetail }) {
  const payload = document.payload
  return <div className="document-body">
    <p className="document-meta">{[payload.space, payload.page_status || payload.status, payload.version && `v${payload.version}`].filter(Boolean).join(' · ')}</p>
    {payload.body && <p>{payload.body}</p>}
    {payload.sections?.map((section, index) => <section key={index}>
      {section.heading && <h2>{section.heading}</h2>}
      {section.body && <p>{section.body}</p>}
    </section>)}
    {payload.comments?.map((comment, index) => <article className="document-message" key={index}><p>{text(comment)}</p></article>)}
  </div>
}

function SlackView({ document }: { document: DocumentDetail }) {
  const payload = document.payload
  const messages = [...(payload.messages || []), ...(payload.replies || [])]
  return <div className="document-body">
    <p className="document-meta">{[payload.workspace, payload.channel].filter(Boolean).join(' · ')}</p>
    {payload.text && <article className="document-message"><p>{payload.text}</p></article>}
    {messages.map((message, index) => <article className="document-message" key={index}>
      {typeof message !== 'string' && (message.author || message.author_id) && <strong>{message.author || message.author_id}</strong>}
      <p>{text(message)}</p>
    </article>)}
  </div>
}

function GitHubView({ document }: { document: DocumentDetail }) {
  const payload = document.payload
  return <div className="document-body">
    <p className="document-meta">
      {payload.repository && <span>{payload.repository}</span>}
      <>{payload.number !== undefined && ` · #${payload.number}`}</>
      {(payload.record_type || payload.type) && <> · <span>{payload.record_type || payload.type}</span></>}
      {(payload.review_state || payload.state) && ` · ${payload.review_state || payload.state}`}
    </p>
    {payload.body && <p>{payload.body}</p>}
    {Boolean(payload.commit_ids?.length) && <p><strong>Commits:</strong> {payload.commit_ids?.join(', ')}</p>}
    {payload.merge_version && <p><strong>Merge version:</strong> {payload.merge_version}</p>}
    {[...(payload.reviews || []), ...(payload.comments || [])].map((message, index) =>
      <article className="document-message" key={index}><p>{text(message)}</p></article>)}
  </div>
}

export default function DocumentPanel({ item, userId, onClose }: Props) {
  const [document, setDocument] = useState<DocumentDetail | null>(null)
  const [error, setError] = useState('')
  const closeButton = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    let current = true
    setDocument(null)
    setError('')
    getDocument(item.source, item.external_id, userId)
      .then((value) => { if (current) setDocument(value) })
      .catch(() => { if (current) setError('This document is unavailable.') })
    return () => { current = false }
  }, [item.source, item.external_id, userId])

  useEffect(() => {
    closeButton.current?.focus()
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', escape)
    return () => window.removeEventListener('keydown', escape)
  }, [onClose])

  const View = item.source === 'jira' ? JiraView
    : item.source === 'confluence' ? ConfluenceView
      : item.source === 'slack' ? SlackView : GitHubView

  return <div
    className="document-backdrop"
    data-testid="document-backdrop"
    onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}
  >
    <aside className={`document-panel document-${item.source}`} role="dialog" aria-modal="true" aria-labelledby="document-title">
      <button ref={closeButton} className="document-close" aria-label="Close document" onClick={onClose}>×</button>
      <p className="document-source">{item.source}</p>
      <h1 id="document-title">{document?.title || item.title}</h1>
      {!document && !error && <p role="status">Loading document…</p>}
      {error && <p role="alert">{error}</p>}
      {document && <View document={document} />}
    </aside>
  </div>
}
