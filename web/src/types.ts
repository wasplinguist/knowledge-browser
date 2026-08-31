export type Source = 'jira' | 'confluence' | 'slack' | 'github'

export type DemoUser = { id: string; name: string; email: string }
export type SearchItem = {
  external_id: string
  title: string
  source: Source
  matched_field: string
  excerpt: string
  author?: string | null
  matched_author?: string | null
  container?: string | null
  updated_at?: string | null
  url?: string | null
  score: number
}
export type SearchResponse = {
  items: SearchItem[]
  facets: Record<Source, number>
  search_id: string | null
  profile: string
}
export type Citation = {
  source?: Source
  external_id?: string
  title?: string
  url?: string | null
}
export type AnswerResponse = {
  answer: string | null
  citations: Citation[]
  follow_ups: string[]
  error?: { code: string; message: string }
}
export type ApiError = { error: { code: string; message: string } }

export type PersonText = string | {
  author?: string
  author_id?: string
  body?: string
  text?: string
}
export type DocumentPayload = {
  issue_key?: string
  status?: string
  priority?: string
  description?: string
  components?: string[]
  affected_versions?: string[]
  fix_versions?: string[]
  comments?: PersonText[]
  space?: string
  page_status?: string
  version?: number
  body?: string
  sections?: Array<{ heading?: string; body?: string }>
  workspace?: string
  channel?: string
  text?: string
  messages?: PersonText[]
  replies?: PersonText[]
  repository?: string
  number?: number
  review_state?: string
  state?: string
  commit_ids?: string[]
  merge_version?: string
  reviews?: PersonText[]
}
export type DocumentDetail = {
  source: Source
  external_id: string
  kind: string
  title: string
  author?: string | null
  container?: string | null
  created_at?: string | null
  updated_at?: string | null
  payload: DocumentPayload
}
export type DocumentSelection = Pick<DocumentDetail, 'source' | 'external_id' | 'title'>
