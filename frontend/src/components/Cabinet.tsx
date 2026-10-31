import { useRef, useState, type ReactNode } from 'react'
import type { AuthUser, DocInfo, UploadTarget } from '../types'

interface Props {
  docs: DocInfo[]
  loading: boolean
  uploading: boolean
  error: string
  notice: string
  onUpload: (files: File[], target?: UploadTarget, effectiveDate?: string) => void
  user?: AuthUser | null // set when accounts are on
  onDelete: (doc: DocInfo) => void
  children?: ReactNode // rendered above the archive (the conversations list)
}

export function Cabinet({ docs, loading, uploading, error, notice, onUpload, onDelete, user, children }: Props) {
  const input = useRef<HTMLInputElement>(null)
  const [who, setWho] = useState('private') // private | shared | team:<id>
  const canUpload = !user || user.role !== 'viewer'
  const [effective, setEffective] = useState('') // optional: when the policy took effect
  const send = (files: File[]) => {
    const date = effective || undefined
    setEffective('') // applies to this upload only
    if (!user) return onUpload(files, undefined, date)
    onUpload(files, who.startsWith('team:') ? { scope: 'team', teamId: Number(who.slice(5)) } : { scope: who === 'shared' ? 'shared' : 'private' }, date)
  }
  const [over, setOver] = useState(false)
  const chunks = docs.reduce((n, d) => n + d.chunks, 0)

  return (
    <aside className="cabinet" aria-label="Document archive">
      {children}
      <h2 className="eyebrow">The archive</h2>
      <p className="cabinet-count">
        <b>{docs.length}</b> documents <span>·</span> <b>{chunks}</b> passages
      </p>

      <ul className="folders">
        {loading && <li className="folder-empty">Opening drawers…</li>}
        {!loading && docs.length === 0 && <li className="folder-empty">The archive is empty. File a document below.</li>}
        {docs.map((d, i) => (
          <li key={d.id} className="folder" style={{ animationDelay: `${i * 50}ms` }}>
            <span className="folder-tab" aria-hidden="true" />
            <span className="folder-name">{d.title}</span>
            <span className="folder-meta" title={d.scope === 'team' ? `Visible to the ${d.team} team` : d.private ? 'Only you can see and search this document' : undefined}>
              {d.chunks} {d.chunks === 1 ? 'passage' : 'passages'}
              {d.versions && d.versions > 1 ? ` · v${d.version}` : ''}
              {d.updated_at ? ` · ${d.updated_at}` : ''}
              {d.scope === 'team' ? ` · team ${d.team}` : d.private && ' · private'}
            </span>
            {d.stale && (
              <span className="folder-flag" title="Not updated for a long time. Answers citing it carry a warning.">
                ⚠ may be outdated
              </span>
            )}
            {!!d.quarantined && (
              <span className="folder-flag" title="Some passages look like prompt injection and are never used for answers">
                ⚠ {d.quarantined} quarantined
              </span>
            )}
            <button className="folder-del" aria-label={`Delete ${d.title}`} title="Delete this document" onClick={() => onDelete(d)}>
              ×
            </button>
          </li>
        ))}
      </ul>

      {!canUpload && <p className="folder-empty">Your role can read and ask, but not upload.</p>}
      {canUpload && user && (
        <label className="scope-pick">
          Who can see new uploads
          <select value={who} onChange={(e) => setWho(e.target.value)} data-testid="scope-select">
            <option value="private">Only me</option>
            {user.teams.map((t) => (
              <option key={t.id} value={`team:${t.id}`}>
                Team {t.name}
              </option>
            ))}
            {user.role === 'admin' && <option value="shared">Everyone</option>}
          </select>
        </label>
      )}
      {canUpload && (
        <label className="scope-pick">
          Policy effective date (optional)
          <input type="date" value={effective} onChange={(e) => setEffective(e.target.value)} data-testid="effective-date" />
        </label>
      )}
      <div
        hidden={!canUpload}
        className={`drop ${over ? 'drop-over' : ''}`}
        onDragOver={(e) => {
          e.preventDefault()
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setOver(false)
          if (canUpload) send(Array.from(e.dataTransfer.files))
        }}
      >
        <button className="drop-btn" onClick={() => input.current?.click()} disabled={uploading}>
          {uploading ? 'Filing…' : 'File a document'}
        </button>
        <small>Drop .pdf .md .txt here · 5 MB max</small>
        <input
          ref={input}
          type="file"
          hidden
          multiple
          accept=".pdf,.md,.txt"
          data-testid="file-input"
          onChange={(e) => {
            send(Array.from(e.target.files ?? []))
            e.target.value = ''
          }}
        />
      </div>
      {notice && (
        <p className="notice" role="status" data-testid="notice">
          {notice}
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </aside>
  )
}
