import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { chatStream } from './api'

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
