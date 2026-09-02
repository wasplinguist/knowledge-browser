import { FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'

import { answer, getDemoUsers, recordClick, search } from './api'
import DocumentPanel from './DocumentPanel'
import './styles.css'
import type {
  AnswerResponse,
  Citation,
  DemoUser,
  DocumentSelection,
  SearchItem,
  Source,
} from './types'

type FollowUp = {
  id: number
  question: string
  result?: AnswerResponse
  error?: string
}

const sources: Array<Source | 'all'> = ['all', 'confluence', 'jira', 'github', 'slack']
const examples = [
  'What is the latest project update?',
  'Why was the launch delayed?',
  'Who owns the remaining blockers?',
]
const sourceName = (source: string) =>
  source === 'all' ? 'All' : source[0].toUpperCase() + source.slice(1)
const matchLabel = (field: string) => `Matched in ${field.replace(/_/g, ' ')}`
const displayDate = (value?: string | null) => value
  ? new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(new Date(value))
  : 'Recently updated'

function uniqueCitations(citations: Citation[]) {
  const urls = new Set<string>()
  const documents = new Set<string>()
  return citations.filter((citation) => {
    const url = citation.url || undefined
    const document = citation.source && citation.external_id
      ? `${citation.source}:${citation.external_id}`
      : undefined
    if (url && urls.has(url) || document && documents.has(document)) return false
    if (url) urls.add(url)
    if (document) documents.add(document)
    return true
  })
}

const evidenceLabels = {
  complete: 'Evidence checked',
  incomplete: 'Some information may be missing',
  conflicting: 'Sources conflict',
} as const

function citationKey(citation: Citation) {
  return citation.source && citation.external_id
    ? `${citation.source}:${citation.external_id}`
    : citation.url || citation.chunk_id
}

function GroundedAnswer({
  result,
  onCitation,
}: {
  result: AnswerResponse
  onCitation: (citation: Citation, button: HTMLButtonElement) => void
}) {
  const inlineCitation = (number: number) => {
    const documents = uniqueCitations(result.citations)
    const citation = result.citations[number - 1]
    if (!citation?.source || !citation.external_id) return null
    const documentIndex = documents.findIndex(
      (document) => citationKey(document) === citationKey(citation),
    )
    const label = documentIndex < 0 ? number : documentIndex + 1
    return <button
      type="button"
      className="inline-citation"
      aria-label={`Citation ${label}: ${citation.title || citation.external_id}`}
      onClick={(event) => onCitation(citation, event.currentTarget)}
    >[{label}]</button>
  }

  return <>
    {result.answer && <div className="answer-copy">
      <ReactMarkdown
        skipHtml
        remarkPlugins={[citationLinks]}
        components={{
          a: ({ href, children }) => {
            const citation = href?.match(/^#citation-(\d+)$/)
            if (citation) return inlineCitation(Number(citation[1])) ?? <>{children}</>
            return <a href={href} target="_blank" rel="noreferrer">{children}</a>
          },
          img: ({ alt }) => <span className="markdown-image-alt">{alt || 'Image'}</span>,
        }}
      >{result.answer}</ReactMarkdown>
    </div>}
    {!!result.conflicts?.length && <aside className="answer-note conflict">
      <h3>Conflicting evidence</h3>
      <ul>{result.conflicts.map((conflict, index) =>
        <li key={index}>{conflict.description}</li>)}</ul>
    </aside>}
    {!!result.missing_information?.length && <aside className="answer-note">
      <h3>Missing information</h3>
      <ul>{result.missing_information.map((item, index) =>
        <li key={index}>{item}</li>)}</ul>
    </aside>}
    {!!result.citations.length && <section className="citations" aria-label="Sources">
      <h3>Sources</h3>
      <div className="citation-list">{uniqueCitations(result.citations).map((citation, index) =>
        <button
          type="button"
          className="citation"
          key={citationKey(citation) || index}
          onClick={(event) => onCitation(citation, event.currentTarget)}
        >
          <span className="citation-number">{index + 1}</span>
          <span><strong>{citation.source ? sourceName(citation.source) : 'Source'}</strong>
            <small>{[citation.external_id, citation.title].filter(Boolean).join(' · ') || 'Supporting evidence'}</small>
          </span>
        </button>)}</div>
    </section>}
  </>
}

type MarkdownNode = {
  type: string
  value?: string
  url?: string
  children?: MarkdownNode[]
}

function citationLinks() {
  return (tree: MarkdownNode) => {
    function visit(parent: MarkdownNode) {
      if (!parent.children) return
      const children: MarkdownNode[] = []
      for (const child of parent.children) {
        if (child.type !== 'text' || !child.value || parent.type === 'link' || parent.type === 'linkReference') {
          visit(child)
          children.push(child)
          continue
        }
        let cursor = 0
        for (const match of child.value.matchAll(/\[(\d+)\]/g)) {
          if (match.index > cursor) {
            children.push({ type: 'text', value: child.value.slice(cursor, match.index) })
          }
          children.push({
            type: 'link',
            url: `#citation-${match[1]}`,
            children: [{ type: 'text', value: match[0] }],
          })
          cursor = match.index + match[0].length
        }
        if (cursor < child.value.length) {
          children.push({ type: 'text', value: child.value.slice(cursor) })
        }
      }
      parent.children = children
    }
    visit(tree)
  }
}

export default function App() {
  const [users, setUsers] = useState<DemoUser[]>([])
  const [userId, setUserId] = useState('')
  const [query, setQuery] = useState('')
  const [source, setSource] = useState<Source>()
  const [items, setItems] = useState<SearchItem[]>([])
  const [facets, setFacets] = useState<Record<string, number>>({})
  const [searchId, setSearchId] = useState<string | null>(null)
  const [answerResult, setAnswerResult] = useState<AnswerResponse | null>(null)
  const [answerError, setAnswerError] = useState('')
  const [followUps, setFollowUps] = useState<FollowUp[]>([])
  const [followUpQuestion, setFollowUpQuestion] = useState('')
  const [followUpLoading, setFollowUpLoading] = useState(false)
  const [searchError, setSearchError] = useState('')
  const [loading, setLoading] = useState(false)
  const [hasSearched, setHasSearched] = useState(false)
  const [selectedDocument, setSelectedDocument] = useState<DocumentSelection | null>(null)
  const selectedResult = useRef<HTMLButtonElement | null>(null)
  const followUpHeading = useRef<HTMLHeadingElement | null>(null)
  const requestId = useRef(0)
  const followUpId = useRef(0)
  const [searchSessionId] = useState(() => {
    const key = 'knowledge-browser-session-id'
    const existing = localStorage.getItem(key)
    if (existing) return existing
    const value = crypto.randomUUID()
    localStorage.setItem(key, value)
    return value
  })

  useEffect(() => {
    getDemoUsers()
      .then(({ items: demoUsers }) => {
        setUsers(demoUsers)
        setUserId(demoUsers[0]?.id ?? '')
      })
      .catch(() => setSearchError('Database connection error.'))
  }, [])

  useEffect(() => {
    if (followUps.length) followUpHeading.current?.focus()
  }, [followUps.length])

  function clearResults() {
    setItems([])
    setFacets({})
    setSearchId(null)
    setAnswerResult(null)
    setAnswerError('')
    setFollowUps([])
    setFollowUpQuestion('')
    setFollowUpLoading(false)
    setSearchError('')
    setHasSearched(false)
    setLoading(false)
    setSelectedDocument(null)
    return ++requestId.current
  }

  async function runSearch(nextQuery = query, nextUserId = userId, nextSource = source) {
    const trimmed = nextQuery.trim()
    if (!trimmed || !nextUserId) return
    const id = clearResults()
    setLoading(true)
    try {
      const result = await search(trimmed, nextUserId, nextSource, searchSessionId)
      if (id !== requestId.current) return
      setItems(result.items)
      setFacets(result.facets)
      setSearchId(result.search_id)
      setHasSearched(true)
      answer(trimmed, nextUserId, nextSource)
        .then((value) => {
          if (id !== requestId.current) return
          if (value.error || !value.answer) {
            setAnswerError(value.error?.message || 'AI answer is unavailable.')
          } else {
            setAnswerResult(value)
          }
        })
        .catch(() => { if (id === requestId.current) setAnswerError('AI answer is unavailable.') })
    } catch (error) {
      if (id !== requestId.current) return
      setSearchError(error instanceof Error ? error.message : 'Search is unavailable.')
    } finally {
      if (id === requestId.current) setLoading(false)
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    void runSearch()
  }

  async function askFollowUp(question: string) {
    const trimmed = question.trim()
    if (!trimmed || !userId || followUpLoading) return
    const id = ++followUpId.current
    const activeRequest = requestId.current
    setFollowUps((current) => [...current, { id, question: trimmed }])
    setFollowUpQuestion('')
    setFollowUpLoading(true)
    try {
      const value = await answer(trimmed, userId, source)
      if (activeRequest !== requestId.current) return
      setFollowUps((current) => current.map((entry) => entry.id === id
        ? value.error || !value.answer
          ? { ...entry, error: value.error?.message || 'AI answer is unavailable.' }
          : { ...entry, result: value }
        : entry))
    } catch {
      if (activeRequest === requestId.current) {
        setFollowUps((current) => current.map((entry) => entry.id === id
          ? { ...entry, error: 'AI answer is unavailable.' }
          : entry))
      }
    } finally {
      if (activeRequest === requestId.current) setFollowUpLoading(false)
    }
  }

  function submitFollowUp(event: FormEvent) {
    event.preventDefault()
    void askFollowUp(followUpQuestion)
  }

  function chooseSource(next: Source | 'all') {
    const value = next === 'all' ? undefined : next
    setSource(value)
    if (query.trim()) void runSearch(query, userId, value)
  }

  function openDocument(item: DocumentSelection, button?: HTMLButtonElement) {
    if (button) selectedResult.current = button
    setSelectedDocument(item)
  }

  const closeDocument = useCallback(() => {
    setSelectedDocument(null)
    selectedResult.current?.focus()
  }, [])
  const currentUser = users.find((user) => user.id === userId)
  const openCitation = (citation: Citation, button: HTMLButtonElement) => {
    if (!citation.source || !citation.external_id) return
    openDocument({
      source: citation.source,
      external_id: citation.external_id,
      title: citation.title || citation.external_id,
    }, button)
  }
  const latestAnswer = followUps.reduce<AnswerResponse | null>(
    (latest, entry) => entry.result || latest,
    answerResult,
  )

  return <div className="shell">
    <header className="topbar">
      <div className="search-row">
        <form className="searchbox" onSubmit={submit}>
          <span aria-hidden="true">⌕</span>
          <input
            type="search"
            role="searchbox"
            aria-label="Search company knowledge"
            placeholder="Search company knowledge"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <button type="button" aria-label="Clear search" onClick={() => setQuery('')}>×</button>
        </form>
        <label className="profile">
          Demo user
          <select
            aria-label="Demo user"
            value={userId}
            onChange={(event) => {
              const value = event.target.value
              setUserId(value)
              if (query.trim()) void runSearch(query, value)
            }}
          >
            {users.map((user) => <option value={user.id} key={user.id}>{user.name}</option>)}
          </select>
        </label>
      </div>
      {users.length > 0 && <p className="identity-note">Demo identity only — not real login.</p>}
    </header>

    <div className="layout">
      <main>
        {!hasSearched && !loading && <section className="empty">
          <h1>Search company knowledge</h1>
          <p>Find decisions, incidents, plans, conversations, and code.</p>
          <div>{examples.map((example) =>
            <button key={example} onClick={() => { setQuery(example); void runSearch(example) }}>{example}</button>)}</div>
        </section>}

        {loading && <p className="status" role="status">Searching company knowledge…</p>}
        {searchError && <p className="error" role="alert">{searchError}</p>}

        {hasSearched && !searchError && <>
          <section className="answer-panel" aria-label="AI answer">
            <div className="answer-section">
              <div className="answer-heading">
                <h2><span className="orb">✦</span>AI Answer</h2>
                {answerResult?.evidence_status && <span className={`evidence-state ${answerResult.evidence_status}`}>
                  {evidenceLabels[answerResult.evidence_status]}
                </span>}
              </div>
              {answerResult && <GroundedAnswer result={answerResult} onCitation={openCitation} />}
              {!answerResult && !answerError && <p className="muted">Generating a grounded answer…</p>}
              {answerError && <p className="answer-error">{answerError}</p>}
              {followUps.map((entry, index) => <section className="follow-up" key={entry.id} aria-label={`Follow-up: ${entry.question}`}>
                <div className="follow-up-heading">
                  <h3
                    ref={index === followUps.length - 1 ? followUpHeading : undefined}
                    tabIndex={-1}
                  >{entry.question}</h3>
                  {entry.result?.evidence_status && <span className={`evidence-state ${entry.result.evidence_status}`}>
                    {evidenceLabels[entry.result.evidence_status]}
                  </span>}
                </div>
                {!entry.result && !entry.error && <p className="muted" role="status">Generating a grounded answer…</p>}
                {entry.result && <GroundedAnswer result={entry.result} onCitation={openCitation} />}
                {entry.error && <p className="answer-error" role="alert">{entry.error}</p>}
              </section>)}
              {answerResult && <section className="ask-next" aria-label="Ask a follow-up question">
                {!!latestAnswer?.follow_ups.length && <div className="suggestions" aria-label="Suggested follow-up questions">
                <h3>Ask next</h3>
                {latestAnswer.follow_ups.map((question) => <button
                  type="button"
                  key={question}
                  disabled={followUpLoading}
                  onClick={() => void askFollowUp(question)}
                >{question}</button>)}
                </div>}
                <form className="follow-up-form" onSubmit={submitFollowUp}>
                  <input
                    aria-label="Ask a follow-up question"
                    placeholder="Ask anything else"
                    value={followUpQuestion}
                    disabled={followUpLoading}
                    onChange={(event) => setFollowUpQuestion(event.target.value)}
                  />
                  <button type="submit" disabled={followUpLoading || !followUpQuestion.trim()}>Ask</button>
                </form>
              </section>}
            </div>
          </section>

          <p className="result-count">{items.length} matching {items.length === 1 ? 'document' : 'documents'}</p>
          {items.length === 0 && <section className="no-results">
            <h2>No results found</h2>
            <p>Try a different query or clear the source filter.</p>
            {source && <button onClick={() => chooseSource('all')}>Clear source filter</button>}
          </section>}
          <section aria-label="Search results">
            {items.map((item, index) => <article className="result" key={`${item.source}-${item.external_id}`}>
              <div className="source-icon">{item.source[0].toUpperCase()}</div>
              <div>
                <h2><button
                  className="result-title"
                  onClick={(event) => {
                    openDocument(item, event.currentTarget)
                    if (searchId) void recordClick(searchId, userId, item.source, item.external_id, index + 1).catch(() => {})
                  }}
                >{item.title}</button></h2>
                <p className="meta">{sourceName(item.source)} · {displayDate(item.updated_at)} · {item.author || 'Company knowledge'} · {item.container || 'Company knowledge'}</p>
                <p className="matched">{matchLabel(item.matched_field)}{item.matched_author ? ` by ${item.matched_author}` : ''}</p>
                <p>{item.excerpt}</p>
              </div>
            </article>)}
          </section>
        </>}
      </main>

      {hasSearched && <aside className="facets" aria-label="Result sources">
        <p>Found {items.length} results</p>
        {sources.map((item) => <button
          key={item}
          className={(item === 'all' && !source) || item === source ? 'selected' : ''}
          aria-pressed={(item === 'all' && !source) || item === source}
          onClick={() => chooseSource(item)}
        >
          <span>{sourceName(item)}</span>
          <span>{item === 'all' ? items.length : facets[item] || 0}</span>
        </button>)}
      </aside>}
    </div>

    {currentUser && <footer>Viewing as {currentUser.name} · {currentUser.email}</footer>}
    {selectedDocument && <DocumentPanel item={selectedDocument} userId={userId} onClose={closeDocument} />}
  </div>
}
