import type { SessionInfo } from '../types'

interface Props {
  sessions: SessionInfo[]
  activeId: string | null
  busy: boolean
  onNew: () => void
  onOpen: (id: string) => void
  onDelete: (s: SessionInfo) => void
}

export function History({ sessions, activeId, busy, onNew, onOpen, onDelete }: Props) {
  return (
    <section className="history" aria-label="Conversations">
      <div className="history-head">
        <h2 className="eyebrow">Conversations</h2>
        <button className="history-new" onClick={onNew} disabled={busy} data-testid="new-chat">
          + New
        </button>
      </div>
      <ul>
        {sessions.length === 0 && <li className="folder-empty">Nothing yet. Ask a question.</li>}
        {sessions.map((s) => (
          <li key={s.id} className={s.id === activeId ? 'history-item on' : 'history-item'}>
            <button className="history-open" onClick={() => onOpen(s.id)} disabled={busy} title={s.title}>
              {s.title}
            </button>
            <button className="folder-del" aria-label={`Delete conversation ${s.title}`} onClick={() => onDelete(s)}>
              ×
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}
