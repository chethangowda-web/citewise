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
  Escalation,
  FeedbackReview,
  MyUsage,
  UsageReport,
  SessionDetail,
  SessionInfo,
  Role,
  Stage,
  UploadJob,
  UploadResult,
  UploadTarget,
} from './types'

export const UNAUTH_EVENT = 'policy-desk-unauth'

function headers(extra: Record<string, string> = {}) {
  // The session lives in an HttpOnly cookie that scripts cannot read. This header asks the server for a cookie
  // session and proves the request comes from our own page (cross-site forms cannot set it): CSRF protection.
  const out: Record<string, string> = { ...extra, 'X-Requested-With': 'citewise' }
  const key = localStorage.getItem('policyDeskApiKey')
  if (key) out['X-API-Key'] = key
  return out
}

/** What a failed request says when the server sent no message of its own. 502-504 mean the API is not answering,
 * which on a free host usually means it is waking from sleep. */
export function failureText(status: number): string {
  return [502, 503, 504].includes(status)
    ? 'The server is waking up (free hosting sleeps when idle). Give it about a minute, then try again.'
    : `Request failed (${status})`
}

async function fail(r: Response): Promise<never> {
  let detail = failureText(r.status)
  try {
    const body = await r.json()
    if (typeof body.detail === 'string') detail = body.detail
  } catch {
    /* non-JSON error body */
  }
  // An expired or revoked session (the API marks these with a Bearer challenge; a wrong admin key is not one):
  // tell the app to show the sign-in screen again.
  if (r.status === 401 && r.headers.get('WWW-Authenticate')?.includes('Bearer')) {
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
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ email, password }),
  }).then(async (r) => {
    if (!r.ok) {
      // a wrong password must show its message, not look like an expired session
      let detail = failureText(r.status)
      try {
        const body = await r.json()
        if (typeof body.detail === 'string') detail = body.detail
      } catch {
        /* non-JSON error body */
      }
      throw new Error(detail)
    }
    return (await r.json()) as { user: AuthUser }
  })

// Not signed in is a normal answer here, not an expired session, so this never raises the sign-in event.
export const me = () => fetch('/api/auth/me', { headers: headers() }).then((r) => (r.ok ? (r.json() as Promise<AuthUser | null>) : null))

const authPost = (path: string, body?: unknown) =>
  fetch(`/api/auth/${path}`, {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then(async (r) => {
    if (!r.ok) await fail(r)
  })

export const sendFeedback = (messageId: number, rating: 'up' | 'down' | 'none', reason?: string) =>
  fetch(`/api/messages/${messageId}/feedback`, {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ rating, reason }),
  }).then(async (r) => {
    if (!r.ok) await fail(r)
  })

export const askAPerson = (messageId: number, note: string) =>
  fetch('/api/escalations', {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ message_id: messageId, note }),
  }).then((r) => parse<Escalation & { already_sent: boolean }>(r))

export const myUsage = () => fetch('/api/usage/me', { headers: headers() }).then((r) => parse<MyUsage>(r))

export const myEscalations = () => fetch('/api/escalations', { headers: headers() }).then((r) => parse<Escalation[]>(r))

/** Save a response as a file. Fetched with the page's credentials, so it works for a cookie session or an admin key. */
async function download(url: string, init: RequestInit, fallbackName: string) {
  const r = await fetch(url, init)
  if (!r.ok) return fail(r)
  const name = /filename="([^"]+)"/.exec(r.headers.get('Content-Disposition') ?? '')?.[1] ?? fallbackName
  const href = URL.createObjectURL(await r.blob())
  const a = document.createElement('a')
  a.href = href
  a.download = name
  a.click()
  URL.revokeObjectURL(href)
}
export const downloadMyData = () => download('/api/auth/me/export', { headers: headers() }, 'citewise-my-data.json')
export const downloadAuditCsv = (key = '') => download('/api/admin/logs/export', { headers: adminHeaders(key) }, 'citewise-audit.csv')
export const deleteAccount = (password: string) =>
  fetch('/api/auth/me', {
    method: 'DELETE',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ password }),
  }).then(async (r) => {
    if (!r.ok) await fail(r)
  })

export const logout = () => authPost('logout').catch(() => undefined)
export const logoutAll = () => authPost('logout-all')
export const changePassword = (current_password: string, new_password: string) =>
  authPost('change-password', { current_password, new_password })
export const forgotPassword = (email: string) => authPost('forgot', { email })
export const resetPassword = (token: string, password: string) => authPost('reset', { token, password })

export const listSessions = () => fetch('/api/sessions', { headers: headers() }).then((r) => parse<SessionInfo[]>(r))

export const getSession = (id: string) => fetch(`/api/sessions/${id}`, { headers: headers() }).then((r) => parse<SessionDetail>(r))

export const deleteSession = (id: string) =>
  fetch(`/api/sessions/${id}`, { method: 'DELETE', headers: headers() }).then(async (r) => {
    if (!r.ok) await fail(r)
  })

export const listDocuments = () => fetch('/api/documents', { headers: headers() }).then((r) => parse<DocInfo[]>(r))

export const uploadDocument = (file: File, target?: UploadTarget, effectiveDate?: string, background = false) => {
  const form = new FormData()
  form.append('file', file)
  if (effectiveDate) form.append('effective_date', effectiveDate)
  if (background) form.append('background', 'true')
  if (target) {
    form.append('scope', target.scope)
    if (target.scope === 'team' && target.teamId != null) form.append('team_id', String(target.teamId))
  }
  return fetch('/api/documents', { method: 'POST', headers: headers(), body: form }).then((r) => parse<UploadResult & { job_id: string }>(r))
}

/** Files bigger than this are filed in the background: embedding every passage can take a while. */
export const BACKGROUND_BYTES = 200_000

export type UploadProgress = 'queued' | 'running'

/**
 * Upload one file and resolve with the result, whichever way it was filed. Small files go straight through.
* Large ones are accepted at once as a job that is polled until it is done, so no request has to stay open.
 */
export async function uploadAndWait(
  file: File,
  target?: UploadTarget,
  effectiveDate?: string,
  onProgress?: (p: UploadProgress) => void,
  pollMs = 1000,
): Promise<UploadResult> {
  if (file.size <= BACKGROUND_BYTES) return uploadDocument(file, target, effectiveDate)
  const { job_id } = await uploadDocument(file, target, effectiveDate, true)
  for (let waited = 0; waited < 15 * 60_000; waited += pollMs) {
    const job = await fetch(`/api/documents/jobs/${job_id}`, { headers: headers() }).then((r) => parse<UploadJob>(r))
    if (job.status === 'done') return job.result as UploadResult
    if (job.status === 'failed') throw new Error(job.error || 'The document could not be filed')
    onProgress?.(job.status)
    await new Promise((r) => setTimeout(r, pollMs))
  }
  throw new Error('Filing is taking too long. Check the archive in a minute.')
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
export const adminFeedback = (rating: 'down' | 'up' | 'all') => adminJson('GET', `/feedback?rating=${rating}`).then((r) => parse<FeedbackReview>(r))
export const adminInbox = (status: 'open' | 'resolved') => adminJson('GET', `/escalations?status=${status}`).then((r) => parse<Escalation[]>(r))
export const adminResolve = (id: number, reply: string) => adminJson('POST', `/escalations/${id}/resolve`, { reply }).then(adminDone)
export const adminUsage = (days: number) => adminJson('GET', `/usage?days=${days}`).then((r) => parse<UsageReport>(r))
export const adminRestore = (id: number) => adminJson('POST', `/documents/${id}/restore`).then(adminDone)
export const adminDocuments = () => adminJson('GET', '/documents').then((r) => parse<AdminDoc[]>(r))
