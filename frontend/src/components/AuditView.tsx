import { useCallback, useEffect, useState } from 'react'
import { adminLogs, adminStats } from '../api'
import type { AdminStats, AuditRow } from '../types'

const OUTCOMES = ['all', 'answered', 'refused', 'blocked'] as const

export function AuditView() {
  const [key, setKey] = useState(() => sessionStorage.getItem('policyDeskAdminKey') ?? '')
  const [input, setInput] = useState('')
  const [stats, setStats] = useState<AdminStats | null>(null)
  const [rows, setRows] = useState<AuditRow[]>([])
  const [filter, setFilter] = useState<(typeof OUTCOMES)[number]>('all')
  const [open, setOpen] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    if (!key) return
    setLoading(true)
    setError('')
    try {
      const [s, l] = await Promise.all([adminStats(key), adminLogs(key, filter === 'all' ? undefined : filter)])
      setStats(s)
      setRows(l)
    } catch (e) {
      setStats(null)
      setRows([])
      const msg = e instanceof Error ? e.message : 'Failed to load'
      setError(msg === 'Not found' ? 'Admin access is not enabled on this server.' : msg)
      if (msg.includes('Invalid')) {
        sessionStorage.removeItem('policyDeskAdminKey')
        setKey('')
      }
    } finally {
      setLoading(false)
    }
  }, [key, filter])

  useEffect(() => {
    void load()
  }, [load])

  if (!key) {
    return (
      <section className="audit audit-lock">
        <h2>
          The <em>ledger.</em>
        </h2>
        <p>Every request is audited: what was asked (personal data already redacted), what the guardrails did, what it cost.</p>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            sessionStorage.setItem('policyDeskAdminKey', input.trim())
            setKey(input.trim())
            setInput('')
          }}
        >
          <label htmlFor="adminkey" className="eyebrow">
            Admin key
          </label>
          <div className="keyrow">
            <input id="adminkey" type="password" autoComplete="off" value={input} onChange={(e) => setInput(e.target.value)} />
            <button type="submit" disabled={!input.trim()}>
              Unlock
            </button>
          </div>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
        </form>
      </section>
    )
  }

  const total = stats?.total ?? 0
  const pct = (n: number) => (total ? `${(n / total) * 100}%` : '0%')

  return (
    <section className="audit" aria-label="Audit ledger">
      <div className="audit-head">
        <h2>
          The <em>ledger.</em>
        </h2>
        <button className="ghost" onClick={() => void load()} disabled={loading}>
          {loading ? 'Refreshing…' : 'Refresh'}
        </button>
        <button
          className="ghost"
          onClick={() => {
            sessionStorage.removeItem('policyDeskAdminKey')
            setKey('')
          }}
        >
          Lock
        </button>
      </div>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      {stats && (
        <>
          <div className="cards" data-testid="stats">
            <div className="card">
              <span>Requests</span>
              <b data-testid="stat-total">{stats.total}</b>
              <small>{stats.last_24h} in the last 24h</small>
            </div>
            <div className="card">
              <span>Latency</span>
              <b>{(stats.latency_ms.p50 / 1000).toFixed(1)}s</b>
              <small>p95 {(stats.latency_ms.p95 / 1000).toFixed(1)}s</small>
            </div>
            <div className="card">
              <span>Tokens</span>
              <b>{(stats.tokens.prompt + stats.tokens.completion).toLocaleString()}</b>
              <small>≈ ${stats.estimated_cost_usd.toFixed(4)}</small>
            </div>
            <div className="card">
              <span>Guardrails</span>
              <b>{stats.guardrails_enabled ? 'On' : 'Off'}</b>
              <small>{Object.keys(stats.guardrail_flags).length} kinds fired</small>
            </div>
          </div>

          <div className="split" aria-label="Outcome split">
            <div className="split-bar">
              <i className="seg seg-answered" style={{ width: pct(stats.outcomes.answered) }} />
              <i className="seg seg-refused" style={{ width: pct(stats.outcomes.refused) }} />
              <i className="seg seg-blocked" style={{ width: pct(stats.outcomes.blocked) }} />
            </div>
            <ul>
              <li className="k-answered">{stats.outcomes.answered} answered</li>
              <li className="k-refused">{stats.outcomes.refused} refused</li>
              <li className="k-blocked">{stats.outcomes.blocked} blocked</li>
            </ul>
          </div>

          {Object.keys(stats.guardrail_flags).length > 0 && (
            <p className="flagtally">
              {Object.entries(stats.guardrail_flags).map(([f, n]) => (
                <span key={f}>
                  {f} <b>{n}</b>
                </span>
              ))}
            </p>
          )}
        </>
      )}

      <div className="filters" role="group" aria-label="Filter by outcome">
        {OUTCOMES.map((o) => (
          <button key={o} className={filter === o ? 'on' : ''} aria-pressed={filter === o} onClick={() => setFilter(o)}>
            {o}
          </button>
        ))}
      </div>

      <ol className="ledger" data-testid="ledger">
        {rows.map((r) => (
          <li key={r.id} className={`entry e-${r.outcome} ${open === r.id ? 'open' : ''}`}>
            <button className="entry-head" aria-expanded={open === r.id} onClick={() => setOpen(open === r.id ? null : r.id)}>
              <span className={`pill pill-${r.outcome}`}>{r.outcome}</span>
              <span className="entry-q">{r.question}</span>
              <span className="entry-meta">
                {(r.latency_ms / 1000).toFixed(1)}s · {r.tokens} tok
              </span>
            </button>
            {open === r.id && (
              <div className="entry-body">
                <p>{r.answer}</p>
                <dl>
                  <dt>Request</dt>
                  <dd>{r.request_id || '—'}</dd>
                  <dt>Flags</dt>
                  <dd>{r.flags.length ? r.flags.join(', ') : 'none'}</dd>
                  <dt>Sources</dt>
                  <dd>{r.sources.length ? r.sources.join(', ') : 'none'}</dd>
                  <dt>Model</dt>
                  <dd>{r.model || '—'}</dd>
                  <dt>When</dt>
                  <dd>{new Date(r.created_at).toLocaleString()}</dd>
                </dl>
              </div>
            )}
          </li>
        ))}
        {!loading && !error && rows.length === 0 && <li className="entry-empty">No requests recorded yet.</li>}
      </ol>
    </section>
  )
}
