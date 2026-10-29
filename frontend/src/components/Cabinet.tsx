import { useRef, useState, type ReactNode } from 'react'
import type { DocInfo } from '../types'

interface Props {
  docs: DocInfo[]
  loading: boolean
  uploading: boolean
  error: string
  notice: string
  onUpload: (files: File[]) => void
  onDelete: (doc: DocInfo) => void
  children?: ReactNode // rendered above the archive (the conversations list)
}

export function Cabinet({ docs, loading, uploading, error, notice, onUpload, onDelete, children }: Props) {
  const input = useRef<HTMLInputElement>(null)
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
            <span className="folder-meta" title={d.private ? 'Only you can see and search this document' : undefined}>
              {d.chunks} {d.chunks === 1 ? 'passage' : 'passages'}
              {d.private && ' · private'}
            </span>
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

      <div
        className={`drop ${over ? 'drop-over' : ''}`}
        onDragOver={(e) => {
          e.preventDefault()
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setOver(false)
          onUpload(Array.from(e.dataTransfer.files))
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
            onUpload(Array.from(e.target.files ?? []))
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
