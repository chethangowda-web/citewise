import { useCallback, useEffect, useState } from 'react'
import {
  adminAddMember,
  adminCreateTeam,
  adminDeleteTeam,
  adminDeleteUser,
  adminDocuments,
  adminRemoveMember,
  adminRestore,
  adminSetRole,
  adminTeams,
  adminUsers,
  deleteDocument,
} from '../api'
import type { AdminDoc, AdminTeam, AdminUser, Role } from '../types'

const ROLES: Role[] = ['admin', 'editor', 'viewer']
const TABS = ['users', 'teams', 'documents'] as const
type Tab = (typeof TABS)[number]

export function AdminView({ meId }: { meId: number }) {
  const [tab, setTab] = useState<Tab>('users')
  const [users, setUsers] = useState<AdminUser[]>([])
  const [teams, setTeams] = useState<AdminTeam[]>([])
  const [docs, setDocs] = useState<AdminDoc[]>([])
  const [error, setError] = useState('')
  const [teamName, setTeamName] = useState('')

  const load = useCallback(async () => {
    try {
      const [u, t, d] = await Promise.all([adminUsers(), adminTeams(), adminDocuments()])
      setUsers(u)
      setTeams(t)
      setDocs(d)
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the admin data')
    }
  }, [])

  // Reload on every tab switch: other people change this data while the console is open.
  useEffect(() => {
    void load()
  }, [load, tab])

  // Run a change, then reload; show the server's reason if it refuses (e.g. "at least one admin").
  const act = async (fn: () => Promise<void>) => {
    try {
      await fn()
      await load()
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That did not work')
    }
  }

  return (
    <section className="admin" aria-label="Administration">
      <h2>
        The <em>office.</em>
      </h2>
      <p className="admin-lede">People, teams and who can see which documents.</p>
      <nav className="admin-tabs" aria-label="Admin sections">
        {TABS.map((t) => (
          <button key={t} className={tab === t ? 'on' : ''} aria-pressed={tab === t} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </nav>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      {tab === 'users' && (
        <table className="admin-table" data-testid="admin-users">
          <thead>
            <tr>
              <th>Email</th>
              <th>Role</th>
              <th>Teams</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td>{u.email}</td>
                <td>
                  <select
                    aria-label={`Role of ${u.email}`}
                    value={u.role}
                    onChange={(e) => void act(() => adminSetRole(u.id, e.target.value as Role))}
                  >
                    {ROLES.map((r) => (
                      <option key={r}>{r}</option>
                    ))}
                  </select>
                </td>
                <td>{u.teams.map((t) => t.name).join(', ') || '–'}</td>
                <td>
                  {u.id !== meId && (
                    <button
                      className="admin-del"
                      aria-label={`Delete ${u.email}`}
                      onClick={() => {
                        if (window.confirm(`Delete ${u.email}? Their private documents and conversations are deleted too.`))
                          void act(() => adminDeleteUser(u.id))
                      }}
                    >
                      Delete
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {tab === 'teams' && (
        <div data-testid="admin-teams">
          <form
            className="admin-form"
            onSubmit={(e) => {
              e.preventDefault()
              const name = teamName.trim()
              if (name) void act(() => adminCreateTeam(name)).then(() => setTeamName(''))
            }}
          >
            <input aria-label="New team name" placeholder="New team, e.g. HR" value={teamName} maxLength={80} onChange={(e) => setTeamName(e.target.value)} />
            <button type="submit" disabled={!teamName.trim()}>
              Create team
            </button>
          </form>
          {teams.length === 0 && <p className="admin-empty">No teams yet.</p>}
          {teams.map((t) => {
            const outside = users.filter((u) => !t.members.some((m) => m.id === u.id))
            return (
              <div key={t.id} className="team-card">
                <div className="team-head">
                  <h3>{t.name}</h3>
                  <span>
                    {t.members.length} members · {t.documents} documents
                  </span>
                  <button
                    className="admin-del"
                    aria-label={`Delete team ${t.name}`}
                    onClick={() => {
                      if (window.confirm(`Delete the team "${t.name}"?`)) void act(() => adminDeleteTeam(t.id))
                    }}
                  >
                    Delete team
                  </button>
                </div>
                <ul>
                  {t.members.map((m) => (
                    <li key={m.id}>
                      {m.email}
                      <button aria-label={`Remove ${m.email} from ${t.name}`} onClick={() => void act(() => adminRemoveMember(t.id, m.id))}>
                        ×
                      </button>
                    </li>
                  ))}
                </ul>
                {outside.length > 0 && (
                  <select
                    aria-label={`Add a member to ${t.name}`}
                    value=""
                    onChange={(e) => e.target.value && void act(() => adminAddMember(t.id, Number(e.target.value)))}
                  >
                    <option value="">Add a member…</option>
                    {outside.map((u) => (
                      <option key={u.id} value={u.id}>
                        {u.email}
                      </option>
                    ))}
                  </select>
                )}
              </div>
            )
          })}
        </div>
      )}

      {tab === 'documents' && (
        <table className="admin-table" data-testid="admin-docs">
          <thead>
            <tr>
              <th>Document</th>
              <th>Version</th>
              <th>Who can see it</th>
              <th>Passages</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {docs.length === 0 && (
              <tr>
                <td colSpan={5}>No documents.</td>
              </tr>
            )}
            {docs.map((d) => (
              <tr key={d.id}>
                <td>{d.title}</td>
                <td>
                  v{d.version}
                  {d.status === 'superseded' && <em> · archived</em>}
                  {d.effective_date && <small> · effective {d.effective_date}</small>}
                </td>
                <td>{d.scope === 'shared' ? 'Everyone' : d.scope === 'team' ? `Team ${d.team}` : `Only ${d.owner ?? 'its owner'}`}</td>
                <td>
                  {d.chunks}
                  {d.quarantined > 0 && ` (${d.quarantined} quarantined)`}
                </td>
                <td>
                  {d.status === 'superseded' && (
                    <button aria-label={`Restore version ${d.version} of ${d.title}`} onClick={() => void act(() => adminRestore(d.id))}>
                      Restore
                    </button>
                  )}
                  <button
                    className="admin-del"
                    aria-label={`Delete ${d.title}`}
                    onClick={() => {
                      if (window.confirm(`Delete "${d.title}" and all of its passages?`)) void act(() => deleteDocument(d.id))
                    }}
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
