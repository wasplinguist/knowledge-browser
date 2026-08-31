import { FormEvent, useCallback, useEffect, useRef, useState } from 'react'

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
  const seen = new Set<string>()
  return citations.filter((citation) => {
    const key = citation.url || `${citation.source || ''}:${citation.external_id || ''}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
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
  const [searchError, setSearchError] = useState('')
  const [loading, setLoading] = useState(false)
  const [hasSearched, setHasSearched] = useState(false)
  const [selectedDocument, setSelectedDocument] = useState<DocumentSelection | null>(null)
  const selectedResult = useRef<HTMLButtonElement | null>(null)
  const requestId = useRef(0)
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

  function clearResults() {
    setItems([])
    setFacets({})
    setSearchId(null)
    setAnswerResult(null)
    setAnswerError('')
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
            onChange={(event) => { setQuery(event.target.value); clearResults() }}
          />
          <button type="button" aria-label="Clear search" onClick={() => { setQuery(''); clearResults() }}>×</button>
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
        {!query && <section className="empty">
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
              <h2><span className="orb">✦</span>AI Answer</h2>
              {answerResult?.answer && <p>{answerResult.answer}</p>}
              {!answerResult && !answerError && <p className="muted">Generating a grounded answer…</p>}
              {answerError && <p className="answer-error">{answerError}</p>}
              {answerResult && <div className="citations">
                {uniqueCitations(answerResult.citations).map((citation, index) =>
                  <button
                    key={citation.url || `${citation.source}:${citation.external_id}` || index}
                    onClick={(event) => {
                      if (citation.source && citation.external_id) {
                        openDocument({
                          source: citation.source,
                          external_id: citation.external_id,
                          title: citation.title || citation.external_id,
                        }, event.currentTarget)
                      }
                    }}
                  >
                    {index + 1}. {citation.title || citation.external_id || 'Source'}
                  </button>)}
              </div>}
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
