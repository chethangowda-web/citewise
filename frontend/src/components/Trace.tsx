import { useEffect, useState } from 'react'
import { buildTrace } from '../lib/text'
import type { ChatResponse, Stage } from '../types'

const IDLE = [
  { id: 'screen', label: 'Screen', note: 'injection, abuse, personal data' },
  { id: 'search', label: 'Search', note: 'hybrid vector + keyword retrieval' },
  { id: 'draft', label: 'Draft', note: 'answer from retrieved passages only' },
  { id: 'verify', label: 'Verify', note: 'every claim needs a real citation' },
]
const STAGE_INDEX: Record<Stage, number> = { screening: 0, understanding: 1, retrieving: 1, drafting: 2, verifying: 3 }
const GLYPH = { pass: '✓', warn: '!', fail: '✕', skipped: '–' } as const

export function Trace({
  res,
  pending,
  runKey,
  stage,
}: {
  res?: ChatResponse
  pending: boolean
  runKey: number
  stage?: Stage
}) {
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (!pending || stage) {
      setTick(0)
      return
    }
    const t = setInterval(() => setTick((x) => (x + 1) % IDLE.length), 1000)
    return () => clearInterval(t)
  }, [pending, stage])

  // The server reports real progress while streaming; without it, fall back to an idle animation.
  const current = pending && stage ? STAGE_INDEX[stage] : tick
  const trace = res && !pending ? buildTrace(res) : null

  return (
    <section className="trace" aria-label="Guardrail trace">
      <h2 className="eyebrow">Inspection trace</h2>
      <ol key={runKey} className={pending ? 'stations working' : 'stations'}>
        {IDLE.map((idle, i) => {
          const s = trace?.stations[i]
          const state = s ? s.state : pending && i === current ? 'active' : pending && i < current ? 'done' : 'idle'
          return (
            <li key={idle.id} className={`station st-${state}`} style={{ animationDelay: `${i * 140}ms` }}>
              <span className="node" aria-hidden="true">
                {s ? GLYPH[s.state] : state === 'active' ? '' : ''}
              </span>
              <div>
                <div className="station-label">{idle.label}</div>
                <div className="station-note">{s ? s.note : pending && i === current ? 'working…' : idle.note}</div>
              </div>
            </li>
          )
        })}
      </ol>
      {res && !pending && (
        <dl className="trace-meta">
          <div>
            <dt>Latency</dt>
            <dd>{(res.latency_ms / 1000).toFixed(2)}s</dd>
          </div>
          <div>
            <dt>Flags</dt>
            <dd>{res.flags.length ? res.flags.join(', ') : 'none'}</dd>
          </div>
        </dl>
      )}
    </section>
  )
}
