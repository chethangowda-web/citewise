import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BACKGROUND_BYTES, chatStream, failureText, uploadAndWait } from './api'

const enc = new TextEncoder()

function sse(event: string, data: unknown) {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
}

/** A fetch() response whose body arrives in the given pieces, however they split the events. */
function respond(pieces: string[], status = 200, headers: Record<string, string> = {}) {
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      for (const p of pieces) c.enqueue(enc.encode(p))
      c.close()
    },
  })
  return new Response(status === 200 ? body : JSON.stringify({ detail: 'nope' }), { status, headers })
}

const DONE = { answer: 'Thirty days [1].', sources: [], flags: [], latency_ms: 5, outcome: 'answered', session_id: 'abc' }

beforeEach(() => {
  const store = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  })
  vi.stubGlobal('window', { dispatchEvent: vi.fn() })
})
afterEach(() => vi.unstubAllGlobals())

describe('chatStream', () => {
  it('reports stages and drafts, then resolves with the verified answer', async () => {
    const all = sse('stage', { stage: 'drafting' }) + sse('draft', { text: 'Thirty' }) + sse('done', DONE)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(respond([all])))
    const stages: string[] = []
    const drafts: string[] = []
    const res = await chatStream('q', null, { onStage: (s) => stages.push(s), onDraft: (d) => drafts.push(d) })
    expect(stages).toEqual(['drafting'])
    expect(drafts).toEqual(['Thirty'])
    expect(res.answer).toBe('Thirty days [1].')
    expect(res.session_id).toBe('abc')
  })

  it('reassembles events that are split across network chunks, even mid-character', async () => {
    const all = sse('draft', { text: 'café ✓' }) + sse('done', DONE)
    const bytes = enc.encode(all)
    // cut at every byte so events, lines and multi-byte characters all straddle chunk boundaries
    const pieces = Array.from(bytes, (_, i) => bytes.slice(i, i + 1))
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        pieces.forEach((p) => c.enqueue(p))
        c.close()
      },
    })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body)))
    const drafts: string[] = []
    const res = await chatStream('q', null, { onDraft: (d) => drafts.push(d) })
    expect(drafts).toEqual(['café ✓'])
    expect(res.outcome).toBe('answered')
  })

  it('throws the server message on an error event', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(respond([sse('error', { detail: 'Model unavailable' })])))
    await expect(chatStream('q', null)).rejects.toThrow('Model unavailable')
  })

  it('throws when the connection closes without an answer', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(respond([sse('stage', { stage: 'screening' })])))
    await expect(chatStream('q', null)).rejects.toThrow(/closed before an answer/)
  })

  it('sends the session id and the CSRF header, and never an Authorization header', async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond([sse('done', DONE)]))
    vi.stubGlobal('fetch', fetchMock)
    await chatStream('hello', 'sess1')
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/chat/stream')
    expect(init.headers['X-Requested-With']).toBe('citewise')
    expect(init.headers.Authorization).toBeUndefined()
    expect(JSON.parse(init.body)).toEqual({ question: 'hello', session_id: 'sess1' })
  })

  it('signals sign-in only for a Bearer-challenged 401', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(respond([], 401, { 'WWW-Authenticate': 'Bearer' })))
    await expect(chatStream('q', null)).rejects.toThrow('nope')
    expect(window.dispatchEvent).toHaveBeenCalledTimes(1)

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(respond([], 401))) // e.g. a wrong API key
    await expect(chatStream('q', null)).rejects.toThrow('nope')
    expect(window.dispatchEvent).toHaveBeenCalledTimes(1) // unchanged
  })

  it('keeps no session token in localStorage', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(respond([sse('done', DONE)])))
    await chatStream('q', null)
    expect(localStorage.getItem('policyDeskToken')).toBeNull()
  })
})

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const RESULT = { id: 7, title: 'Big Policy', chunks: 300, quarantined: 0, version: 1 }
const bigFile = () => new File([new Uint8Array(BACKGROUND_BYTES + 1)], 'big_policy.pdf')

describe('uploadAndWait', () => {
  it('sends a small file straight through, with no background job', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(RESULT))
    vi.stubGlobal('fetch', fetchMock)
    const r = await uploadAndWait(new File(['hello'], 'small.md'))
    expect(r.title).toBe('Big Policy')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect((fetchMock.mock.calls[0][1].body as FormData).get('background')).toBeNull()
  })

  it('files a big one in the background and polls until it is done, reporting progress', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json({ job_id: 'abc', status: 'queued' }, 202))
      .mockResolvedValueOnce(json({ job_id: 'abc', status: 'queued', filename: 'big_policy.pdf', error: '', result: null }))
      .mockResolvedValueOnce(json({ job_id: 'abc', status: 'running', filename: 'big_policy.pdf', error: '', result: null }))
      .mockResolvedValueOnce(json({ job_id: 'abc', status: 'done', filename: 'big_policy.pdf', error: '', result: RESULT }))
    vi.stubGlobal('fetch', fetchMock)
    const seen: string[] = []
    const r = await uploadAndWait(bigFile(), { scope: 'private' }, '2026-01-01', (p) => seen.push(p), 0)
    expect(r).toEqual(RESULT)
    expect(seen).toEqual(['queued', 'running'])
    const form = fetchMock.mock.calls[0][1].body as FormData
    expect(form.get('background')).toBe('true')
    expect(form.get('effective_date')).toBe('2026-01-01')
    expect(fetchMock.mock.calls[1][0]).toBe('/api/documents/jobs/abc')
  })

  it('shows the reason when the job fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(json({ job_id: 'abc', status: 'queued' }, 202))
        .mockResolvedValueOnce(json({ job_id: 'abc', status: 'failed', filename: 'x', error: 'This is not a valid .docx file.', result: null })),
    )
    await expect(uploadAndWait(bigFile(), undefined, undefined, undefined, 0)).rejects.toThrow('This is not a valid .docx file.')
  })

  it('reports a rejected upload straight away, without polling', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ detail: 'File too large (max 5 MB)' }, 413))
    vi.stubGlobal('fetch', fetchMock)
    await expect(uploadAndWait(bigFile(), undefined, undefined, undefined, 0)).rejects.toThrow('File too large')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})

describe('failed requests', () => {
  it('explains a gateway error as the server waking up', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>Bad Gateway</html>', { status: 502 })))
    await expect(chatStream('q', null, {})).rejects.toThrow(/waking up/)
  })

  it('still shows the message the server sends when there is one', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'Daily limit reached' }), { status: 503 })))
    await expect(chatStream('q', null, {})).rejects.toThrow('Daily limit reached')
  })

  it('keeps the plain status for other errors', () => {
    expect(failureText(500)).toBe('Request failed (500)')
    expect(failureText(503)).toMatch(/waking up/)
  })
})
