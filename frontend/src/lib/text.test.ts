import { describe, expect, it } from 'vitest'
import type { ChatResponse } from '../types'
import { buildTrace, maskPII, parseAnswer } from './text'

const res = (flags: string[], answer = 'x'): ChatResponse => ({ answer, sources: [], flags, latency_ms: 1 })

describe('parseAnswer', () => {
  it('splits text and citations', () => {
    expect(parseAnswer('Thirty days [1] or store credit [2].')).toEqual([
      { kind: 'text', value: 'Thirty days ' },
      { kind: 'cite', n: 1 },
      { kind: 'text', value: ' or store credit ' },
      { kind: 'cite', n: 2 },
      { kind: 'text', value: '.' },
    ])
  })
  it('returns plain text when there are no citations', () => {
    expect(parseAnswer('No cites')).toEqual([{ kind: 'text', value: 'No cites' }])
  })
})

describe('maskPII', () => {
  it('masks emails and SSNs only', () => {
    const parts = maskPII('Mail jane@example.com SSN 123-45-6789 please')
    expect(parts.filter((p) => p.masked).map((p) => p.text)).toEqual(['jane@example.com', '123-45-6789'])
    expect(parts.map((p) => p.text).join('')).toBe('Mail jane@example.com SSN 123-45-6789 please')
  })
  it('leaves clean text untouched', () => {
    expect(maskPII('How many days?')).toEqual([{ text: 'How many days?', masked: false }])
  })
})

describe('buildTrace', () => {
  it('clean answer passes every station', () => {
    const t = buildTrace(res([]))
    expect(t.verdict).toBe('answered')
    expect(t.stations.every((s) => s.state === 'pass')).toBe(true)
  })
  it('injection is blocked at screen and skips the rest', () => {
    const t = buildTrace(res(['prompt_injection']))
    expect(t.verdict).toBe('blocked')
    expect(t.stations.map((s) => s.state)).toEqual(['fail', 'skipped', 'skipped', 'skipped'])
  })
  it('no context is refused at search', () => {
    const t = buildTrace(res(['no_relevant_context']))
    expect(t.verdict).toBe('refused')
    expect(t.stations.map((s) => s.state)).toEqual(['pass', 'fail', 'skipped', 'skipped'])
  })
  it('ungrounded answer is refused at verify; pii warns at screen', () => {
    expect(buildTrace(res(['ungrounded_answer'])).stations[3].state).toBe('fail')
    expect(buildTrace(res(['pii_email'])).stations[0].state).toBe('warn')
  })
})
