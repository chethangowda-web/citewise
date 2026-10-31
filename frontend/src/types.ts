export interface Source {
  index: number
  document: string
  source: string
  snippet: string
  similarity: number
}

export interface ChatResponse {
  answer: string
  sources: Source[]
  flags: string[]
  latency_ms: number
  outcome?: 'answered' | 'refused' | 'blocked'
  tokens?: number
  session_id?: string
  interpreted_as?: string // the standalone question searched for, when a follow-up was rewritten
}

export interface DocInfo {
  id: number
  title: string
  source: string
  chunks: number
  quarantined?: number
  private?: boolean
  scope?: Scope
  team?: string | null
}

export interface UploadResult {
  id: number
  title: string
  chunks: number
  quarantined?: number
}

export type Turn = {
  id: number
  question: string
  status: 'pending' | 'done' | 'error'
  res?: ChatResponse
  error?: string
  stage?: Stage // real pipeline progress while pending
  draft?: string // text as it is being written; replaced by the verified answer in `res`
}

export type Stage = 'screening' | 'understanding' | 'retrieving' | 'drafting' | 'verifying'

export interface SessionInfo {
  id: string
  title: string
  updated_at: string
}

export interface SessionDetail {
  id: string
  title: string
  messages: { id: number; role: 'user' | 'assistant'; content: string; meta: Partial<ChatResponse> }[]
}

export type Role = 'admin' | 'editor' | 'viewer'

export interface TeamRef {
  id: number
  name: string
}

export interface AuthUser {
  id: number
  email: string
  role: Role
  teams: TeamRef[]
}

export type Scope = 'private' | 'team' | 'shared'

export interface UploadTarget {
  scope: Scope
  teamId?: number
}

export interface AdminUser extends AuthUser {
  created_at: string
}

export interface AdminTeam extends TeamRef {
  documents: number
  members: { id: number; email: string }[]
}

export interface AdminDoc {
  id: number
  title: string
  source: string
  chunks: number
  scope: Scope
  owner: string | null
  team: string | null
  quarantined: number
  created_at: string
}

export interface AuthConfig {
  auth_enabled: boolean
  allow_signup: boolean
}

export interface AdminStats {
  total: number
  outcomes: { answered: number; refused: number; blocked: number }
  last_24h: number
  latency_ms: { p50: number; p95: number }
  tokens: { prompt: number; completion: number }
  estimated_cost_usd: number
  guardrail_flags: Record<string, number>
  guardrails_enabled: boolean
}

export interface AuditRow {
  id: number
  created_at: string
  request_id: string
  outcome: 'answered' | 'refused' | 'blocked'
  question: string
  answer: string
  flags: string[]
  sources: string[]
  latency_ms: number
  model: string
  tokens: number
}
