import { useState } from 'react'
import { buildTrace, maskPII, parseAnswer, type Verdict } from '../lib/text'
import type { Turn } from '../types'

const STAMP: Record<Verdict, string> = { answered: 'Verified', refused: 'Not on file', blocked: 'Blocked' }

export function TurnView({ turn, selected, onSelect }: { turn: Turn; selected: boolean; onSelect: () => void }) {
  const [lit, setLit] = useState<number | null>(null)
  const [open, setOpen] = useState<Set<number>>(new Set())
  const res = turn.res
  const verdict = res ? buildTrace(res).verdict : null

  const jump = (n: number) => {
    setOpen((o) => new Set(o).add(n))
    document.getElementById(`slip-${turn.id}-${n}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }

  return (
    <article className="turn">
      <p className="memo" data-testid="question">
        <span className="memo-tag">Q</span>
        {maskPII(turn.question).map((p, i) =>
          p.masked ? (
            <span key={i} className="redact" title="Personal data, redacted before it reaches the model">
              {p.text}
            </span>
          ) : (
            <span key={i}>{p.text}</span>
          ),
        )}
      </p>

      <div
        className={`sheet ${selected ? 'selected' : ''} ${verdict ? `v-${verdict}` : ''}`}
        onClick={onSelect}
        data-testid="answer-sheet"
      >
        {turn.status === 'pending' && turn.draft && (
          <p className="answer draft" data-testid="draft">
            {turn.draft.replace(/\[\d+\]/g, '')}
            <span className="draft-note" role="status">
              {turn.stage === 'verifying' ? ' · verifying…' : ' · draft, not yet verified'}
            </span>
          </p>
        )}
        {turn.status === 'pending' && !turn.draft && (
          <p className="pending" role="status">
            <span className="dots" aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
            The clerk is checking the archive
          </p>
        )}
        {turn.status === 'error' && (
          <p className="error" role="alert">
            {turn.error}
          </p>
        )}
        {res && verdict && (
          <>
            <span className={`stamp stamp-${verdict}`} data-testid="verdict">
              {STAMP[verdict]}
            </span>
            <p className="answer" data-testid="answer">
              {parseAnswer(res.answer).map((p, i) =>
                p.kind === 'text' ? (
                  <span key={i}>{p.value}</span>
                ) : (
                  <button
                    key={i}
                    className={`cite ${lit === p.n ? 'cite-lit' : ''}`}
                    onMouseEnter={() => setLit(p.n)}
                    onMouseLeave={() => setLit(null)}
                    onFocus={() => setLit(p.n)}
                    onBlur={() => setLit(null)}
                    onClick={(e) => {
                      e.stopPropagation()
                      jump(p.n)
                    }}
                    aria-label={`Source ${p.n}`}
                  >
                    {p.n}
                  </button>
                ),
              )}
            </p>

            {res.sources.length > 0 && (
              <div className="slips">
                <h3 className="eyebrow">Evidence</h3>
                {res.sources.map((s) => (
                  <div
                    key={s.index}
                    id={`slip-${turn.id}-${s.index}`}
                    className={`slip ${lit === s.index ? 'slip-lit' : ''}`}
                    onMouseEnter={() => setLit(s.index)}
                    onMouseLeave={() => setLit(null)}
                  >
                    <button
                      className="slip-head"
                      aria-expanded={open.has(s.index)}
                      onClick={(e) => {
                        e.stopPropagation()
                        setOpen((o) => {
                          const n = new Set(o)
                          if (n.has(s.index)) n.delete(s.index)
                          else n.add(s.index)
                          return n
                        })
                      }}
                    >
                      <span className="slip-no">{s.index}</span>
                      <span className="slip-title">{s.document}</span>
                      <span className="gauge" title={`Similarity ${s.similarity}`}>
                        <span style={{ width: `${Math.round(Math.min(1, s.similarity) * 100)}%` }} />
                      </span>
                      <span className="slip-pct">{Math.round(s.similarity * 100)}%</span>
                    </button>
                    <blockquote className={open.has(s.index) ? 'open' : ''}>{s.snippet.replace(/^#.*\n+/, '')}</blockquote>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </article>
  )
}
