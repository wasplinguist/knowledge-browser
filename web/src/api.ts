import type {
  AnswerResponse,
  ApiError,
  DemoUser,
  DocumentDetail,
  SearchResponse,
  Source,
} from './types'

async function request<T>(path: string, userId?: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(userId ? { 'X-Demo-User-Id': userId } : {}),
      ...init?.headers,
    },
  })
  const body = await response.json() as T | ApiError
  if (!response.ok) throw new Error((body as ApiError).error.message)
  return body as T
}

export const getDemoUsers = () => request<{ items: DemoUser[] }>('/demo-users')

export const search = (
  query: string,
  userId: string,
  source?: Source,
  sessionId?: string,
) => request<SearchResponse>(
  `/search?q=${encodeURIComponent(query)}${source ? `&source=${source}` : ''}`,
  userId,
  { headers: sessionId ? { 'X-Search-Session-Id': sessionId } : {} },
)

export const answer = (question: string, userId: string, source?: Source) =>
  request<AnswerResponse>('/answer', userId, {
    method: 'POST',
    body: JSON.stringify({ question, source }),
  })

export const recordClick = (
  searchId: string,
  userId: string,
  source: Source,
  externalId: string,
  rank: number,
) => fetch(`/api/search-events/${searchId}/click`, {
  method: 'POST',
  keepalive: true,
  headers: { 'Content-Type': 'application/json', 'X-Demo-User-Id': userId },
  body: JSON.stringify({ source, external_id: externalId, rank }),
}).then(() => undefined)

export const getDocument = (source: Source, externalId: string, userId: string) =>
  request<DocumentDetail>(`/documents/${source}/${encodeURIComponent(externalId)}`, userId)
