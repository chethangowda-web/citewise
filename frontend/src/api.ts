import type { AdminStats, AuditRow, ChatResponse, DocInfo, UploadResult } from './types'

function headers(extra: Record<string, string> = {}) {
  const key = localStorage.getItem('policyDeskApiKey')
  return key ? { ...extra, 'X-API-Key': key } : extra
}

async function parse<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let detail = `Request failed (${r.status})`
    try {
      const body = await r.json()
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail)
  }
  return r.json() as Promise<T>
}

export const chat = (question: string) =>
  fetch('/api/chat', {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ question }),
  }).then((r) => parse<ChatResponse>(r))

export const listDocuments = () => fetch('/api/documents', { headers: headers() }).then((r) => parse<DocInfo[]>(r))

export const uploadDocument = (file: File) => {
  const form = new FormData()
  form.append('file', file)
  return fetch('/api/documents', { method: 'POST', headers: headers(), body: form }).then((r) =>
    parse<UploadResult>(r),
  )
}

export const deleteDocument = (id: number) =>
  fetch(`/api/documents/${id}`, { method: 'DELETE', headers: headers() }).then(async (r) => {
    if (!r.ok) await parse<never>(r)
  })

const adminHeaders = (key: string) => ({ 'X-Admin-Key': key })

export const adminStats = (key: string) =>
  fetch('/api/admin/stats', { headers: adminHeaders(key) }).then((r) => parse<AdminStats>(r))

export const adminLogs = (key: string, outcome?: string) =>
  fetch(`/api/admin/logs?limit=50${outcome ? `&outcome=${outcome}` : ''}`, { headers: adminHeaders(key) }).then((r) =>
    parse<AuditRow[]>(r),
  )
