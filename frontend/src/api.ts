import type {
  AdminDoc,
  AdminStats,
  AdminTeam,
  AdminUser,
  AuditRow,
  AuthConfig,
  AuthUser,
  ChatResponse,
  DocInfo,
  SessionDetail,
  SessionInfo,
  Role,
  Stage,
  UploadResult,
  UploadTarget,
} from './types'

const TOKEN_KEY = 'policyDeskToken'
export const UNAUTH_EVENT = 'policy-desk-unauth'

export const getToken = () => localStorage.getItem(TOKEN_KEY)
export const setToken = (t: string | null) => (t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY))

function headers(extra: Record<string, string> = {}) {
  const out: Record<string, string> = { ...extra }
  const key = localStorage.getItem('policyDeskApiKey')
  if (key) out['X-API-Key'] = key
  const token = getToken()
  if (token) out.Authorization = `Bearer ${token}`
  return out
}

async function fail(r: Response): Promise<never> {
  let detail = `Request failed (${r.status})`
  try {
    const body = await r.json()
    if (typeof body.detail === 'string') detail = body.detail
  } catch {
    /* non-JSON error body */
  }
  // An expired or revoked token (the API marks these with a Bearer challenge; a wrong admin key is not one):
  // tell the app to show the sign-in screen again.
  if (r.status === 401 && getToken() && r.headers.get('WWW-Authenticate')?.includes('Bearer')) {
    setToken(null)
    window.dispatchEvent(new Event(UNAUTH_EVENT))
  }
  throw new Error(detail)
}

async function parse<T>(r: Response): Promise<T> {
  if (!r.ok) return fail(r)
  return r.json() as Promise<T>
}

export interface StreamHandlers {
  onStage?: (stage: Stage) => void
  onDraft?: (text: string) => void
}

/** Ask a question over server-sent events. Resolves with the verified answer, which supersedes any draft. */
export async function chatStream(question: string, sessionId: string | null, h: StreamHandlers = {}): Promise<ChatResponse> {
  const r = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ question, session_id: sessionId }),
  })
  if (!r.ok || !r.body) return fail(r)

  const reader = r.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let result: ChatResponse | null = null
  for (;;) {
    const { value, done } = await reader.read()
    if (value) buffer += decoder.decode(value, { stream: !done })
    let end: number
    while ((end = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, end)
      buffer = buffer.slice(end + 2)
      const event = /^event: (.+)$/m.exec(block)?.[1]
      const raw = /^data: (.+)$/m.exec(block)?.[1]
      if (!event || !raw) continue
      const data = JSON.parse(raw)
      if (event === 'stage') h.onStage?.(data.stage)
      else if (event === 'draft') h.onDraft?.(data.text)
      else if (event === 'done') result = data as ChatResponse
      else if (event === 'error') throw new Error(data.detail)
    }
    if (done) break
  }
  if (!result) throw new Error('The connection closed before an answer arrived')
  return result
}

export const authConfig = () => fetch('/api/auth/config').then((r) => parse<AuthConfig>(r))

export const signIn = (mode: 'login' | 'register', email: string, password: string) =>
  fetch(`/api/auth/${mode}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  }).then(async (r) => {
    if (!r.ok) {
      // a wrong password must show its message, not look like an expired session
      let detail = `Request failed (${r.status})`
      try {
        const body = await r.json()
        if (typeof body.detail === 'string') detail = body.detail
      } catch {
        /* non-JSON error body */
      }
      throw new Error(detail)
    }
    return (await r.json()) as { token: string; user: AuthUser }
  })

export const me = () => fetch('/api/auth/me', { headers: headers() }).then((r) => parse<AuthUser | null>(r))

export const listSessions = () => fetch('/api/sessions', { headers: headers() }).then((r) => parse<SessionInfo[]>(r))

export const getSession = (id: string) => fetch(`/api/sessions/${id}`, { headers: headers() }).then((r) => parse<SessionDetail>(r))

export const deleteSession = (id: string) =>
  fetch(`/api/sessions/${id}`, { method: 'DELETE', headers: headers() }).then(async (r) => {
    if (!r.ok) await fail(r)
  })

export const listDocuments = () => fetch('/api/documents', { headers: headers() }).then((r) => parse<DocInfo[]>(r))

export const uploadDocument = (file: File, target?: UploadTarget, effectiveDate?: string) => {
  const form = new FormData()
  form.append('file', file)
  if (effectiveDate) form.append('effective_date', effectiveDate)
  if (target) {
    form.append('scope', target.scope)
    if (target.scope === 'team' && target.teamId != null) form.append('team_id', String(target.teamId))
  }
  return fetch('/api/documents', { method: 'POST', headers: headers(), body: form }).then((r) => parse<UploadResult>(r))
}

export const deleteDocument = (id: number) =>
  fetch(`/api/documents/${id}`, { method: 'DELETE', headers: headers() }).then(async (r) => {
    if (!r.ok) await fail(r)
  })

// An admin account is recognised by its Bearer token; the admin key (if given) is for script-style access.
const adminHeaders = (key = '') => headers(key ? { 'X-Admin-Key': key } : {})

export const adminStats = (key = '') =>
  fetch('/api/admin/stats', { headers: adminHeaders(key) }).then((r) => parse<AdminStats>(r))

export const adminLogs = (key = '', outcome?: string) =>
  fetch(`/api/admin/logs?limit=50${outcome ? `&outcome=${outcome}` : ''}`, { headers: adminHeaders(key) }).then((r) =>
    parse<AuditRow[]>(r),
  )

const adminJson = (method: string, path: string, body?: unknown) =>
  fetch(`/api/admin${path}`, {
    method,
    headers: adminHeaders(),
    ...(body === undefined ? {} : { body: JSON.stringify(body), headers: headers({ 'Content-Type': 'application/json' }) }),
  })

const adminDone = (r: Response) => (r.ok ? Promise.resolve() : parse<never>(r).then(() => undefined))

export const adminUsers = () => adminJson('GET', '/users').then((r) => parse<AdminUser[]>(r))
export const adminSetRole = (id: number, role: Role) => adminJson('PATCH', `/users/${id}`, { role }).then(adminDone)
export const adminDeleteUser = (id: number) => adminJson('DELETE', `/users/${id}`).then(adminDone)
export const adminTeams = () => adminJson('GET', '/teams').then((r) => parse<AdminTeam[]>(r))
export const adminCreateTeam = (name: string) => adminJson('POST', '/teams', { name }).then(adminDone)
export const adminDeleteTeam = (id: number) => adminJson('DELETE', `/teams/${id}`).then(adminDone)
export const adminAddMember = (team: number, user: number) => adminJson('PUT', `/teams/${team}/members/${user}`).then(adminDone)
export const adminRemoveMember = (team: number, user: number) => adminJson('DELETE', `/teams/${team}/members/${user}`).then(adminDone)
export const adminRestore = (id: number) => adminJson('POST', `/documents/${id}/restore`).then(adminDone)
export const adminDocuments = () => adminJson('GET', '/documents').then((r) => parse<AdminDoc[]>(r))
