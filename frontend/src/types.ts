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
}

export interface DocInfo {
  id: number
  title: string
  source: string
  chunks: number
  quarantined?: number
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
