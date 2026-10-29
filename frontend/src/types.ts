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
}

export interface DocInfo {
  id: number
  title: string
  source: string
  chunks: number
  quarantined?: number
  private?: boolean
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

export type Stage = 'screening' | 'retrieving' | 'drafting' | 'verifying'

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

export interface AuthUser {
  id: number
  email: string
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
