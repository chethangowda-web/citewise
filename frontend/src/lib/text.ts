import type { ChatResponse } from '../types'

export type AnswerPart = { kind: 'text'; value: string } | { kind: 'cite'; n: number }

/** Split an answer into text and [n] citation markers (only valid markers are produced by the API). */
export function parseAnswer(answer: string): AnswerPart[] {
  const parts: AnswerPart[] = []
  let last = 0
  for (const m of answer.matchAll(/\[(\d+)\]/g)) {
    const at = m.index ?? 0
    if (at > last) parts.push({ kind: 'text', value: answer.slice(last, at) })
    parts.push({ kind: 'cite', n: Number(m[1]) })
    last = at + m[0].length
  }
  if (last < answer.length) parts.push({ kind: 'text', value: answer.slice(last) })
  return parts
}

const PII = [
  /[\w.+-]+@[\w-]+\.[\w.-]+/g, // email
  /\b\d{3}-\d{2}-\d{4}\b/g, // SSN
  /\b(?:\d[ -]?){13,16}\b/g, // card
]

export type MaskPart = { text: string; masked: boolean }

/** Mark PII spans in the user's own question so the UI can draw redaction bars over them. */
export function maskPII(text: string): MaskPart[] {
  const spans: [number, number][] = []
  for (const re of PII) for (const m of text.matchAll(re)) spans.push([m.index ?? 0, (m.index ?? 0) + m[0].length])
  spans.sort((a, b) => a[0] - b[0])
  const merged: [number, number][] = []
  for (const s of spans) {
    const prev = merged[merged.length - 1]
    if (prev && s[0] <= prev[1]) prev[1] = Math.max(prev[1], s[1])
    else merged.push([s[0], s[1]])
  }
  const parts: MaskPart[] = []
  let last = 0
  for (const [a, b] of merged) {
    if (a > last) parts.push({ text: text.slice(last, a), masked: false })
    parts.push({ text: text.slice(a, b), masked: true })
    last = b
  }
  if (last < text.length) parts.push({ text: text.slice(last), masked: false })
  return parts
}

export type StationState = 'pass' | 'warn' | 'fail' | 'skipped'
export interface Station {
  id: 'screen' | 'search' | 'draft' | 'verify'
  label: string
  note: string
  state: StationState
}
export type Verdict = 'answered' | 'refused' | 'blocked'

const BLOCKED = ['prompt_injection', 'prompt_injection_ml', 'toxicity', 'empty', 'too_long']

/** Derive what each guardrail stage did from the flags the API returned. */
export function buildTrace(res: ChatResponse): { stations: Station[]; verdict: Verdict } {
  const f = new Set(res.flags)
  const blockedBy = BLOCKED.find((b) => f.has(b)) ?? (res.outcome === 'blocked' ? 'blocked' : undefined)
  const pii = res.flags.some((x) => x.startsWith('pii_'))
  const noContext = f.has('no_relevant_context')
  const ungrounded = f.has('ungrounded_answer')

  const screen: Station = blockedBy
    ? { id: 'screen', label: 'Screen', note: blockedBy.replaceAll('_', ' ') + ' stopped here', state: 'fail' }
    : pii
      ? { id: 'screen', label: 'Screen', note: 'personal data redacted', state: 'warn' }
      : f.has('ml_guard_unavailable')
        ? { id: 'screen', label: 'Screen', note: 'AI detector offline, pattern check only', state: 'warn' }
        : { id: 'screen', label: 'Screen', note: 'question looks clean', state: 'pass' }

  const search: Station = blockedBy
    ? { id: 'search', label: 'Search', note: 'not reached', state: 'skipped' }
    : noContext
      ? { id: 'search', label: 'Search', note: 'nothing relevant in the archive', state: 'fail' }
      : { id: 'search', label: 'Search', note: 'relevant passages found', state: 'pass' }

  const stopped = !!blockedBy || noContext
  const draft: Station = stopped
    ? { id: 'draft', label: 'Draft', note: 'not reached', state: 'skipped' }
    : { id: 'draft', label: 'Draft', note: 'answer written from passages', state: 'pass' }

  const verify: Station = stopped
    ? { id: 'verify', label: 'Verify', note: 'not reached', state: 'skipped' }
    : ungrounded
      ? { id: 'verify', label: 'Verify', note: 'no valid citation, withheld', state: 'fail' }
      : f.has('invalid_citation_removed')
        ? { id: 'verify', label: 'Verify', note: 'fake citation removed', state: 'warn' }
        : { id: 'verify', label: 'Verify', note: 'every claim cited', state: 'pass' }

  const verdict: Verdict = blockedBy ? 'blocked' : noContext || ungrounded ? 'refused' : 'answered'
  return { stations: [screen, search, draft, verify], verdict }
}
