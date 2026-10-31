import { useEffect, useState } from 'react'
import { changePassword, deleteAccount, downloadMyData, logoutAll, myEscalations, myUsage } from '../api'
import type { Escalation, MyUsage } from '../types'

export function AccountPanel({ email, onClose, onSignedOut }: { email: string; onClose: () => void; onSignedOut: () => void }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [asked, setAsked] = useState<Escalation[]>([])
  const [usage, setUsage] = useState<MyUsage | null>(null)
  const [erasing, setErasing] = useState(false)
  const [erasePassword, setErasePassword] = useState('')

  useEffect(() => {
    void myEscalations().then(setAsked).catch(() => undefined)
    void myUsage().then(setUsage).catch(() => undefined)
  }, [])

  const change = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    setMessage('')
    try {
      await changePassword(current, next)
      setCurrent('')
      setNext('')
      setMessage('Password changed. Your other devices were signed out.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not change the password')
    } finally {
      setBusy(false)
    }
  }

  const everywhere = async () => {
    setError('')
    try {
      await logoutAll()
      onSignedOut()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not sign out')
    }
  }

  const erase = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    try {
      await deleteAccount(erasePassword)
      onSignedOut()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete the account')
    }
  }

  return (
    <section className="account-panel" aria-label="Your account">
      <header>
        <strong>{email}</strong>
        <button onClick={onClose} aria-label="Close account panel">
          ×
        </button>
      </header>
      {usage && (
        <div className="usage-me" data-testid="usage-me">
          <h3 className="eyebrow">Your usage, last 24 hours</h3>
          {usage.unlimited ? (
            <p>{usage.used.toLocaleString()} tokens used. No daily limit applies to you.</p>
          ) : (
            <>
              <p>
                {usage.used.toLocaleString()} of {usage.limit.toLocaleString()} tokens used
              </p>
              <div className="meter" role="progressbar" aria-valuenow={usage.used} aria-valuemax={usage.limit}>
                <span style={{ width: `${Math.min(100, (usage.used / usage.limit) * 100)}%` }} className={usage.used >= usage.limit ? 'full' : ''} />
              </div>
            </>
          )}
        </div>
      )}
      <form onSubmit={change}>
        <h3 className="eyebrow">Change password</h3>
        <label>
          Current password
          <input type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
        </label>
        <label>
          New password
          <input type="password" autoComplete="new-password" required minLength={8} value={next} onChange={(e) => setNext(e.target.value)} />
        </label>
        <button type="submit" disabled={busy}>
          Change password
        </button>
      </form>
      {message && (
        <p className="notice" role="status" data-testid="account-message">
          {message}
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {asked.length > 0 && (
        <div className="asked" data-testid="asked">
          <h3 className="eyebrow">Questions you sent to the team</h3>
          <ul>
            {asked.map((q) => (
              <li key={q.id}>
                <b>{q.question}</b>
                <span className={`pill ${q.status}`}>{q.status === 'open' ? 'waiting' : 'answered'}</span>
                {q.reply && <p>{q.reply}</p>}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="account-danger">
        <h3 className="eyebrow">Your data</h3>
        <p>Download everything this system holds about you, or erase your account.</p>
        <button className="plain" onClick={() => void downloadMyData().catch((e) => setError(e instanceof Error ? e.message : 'Download failed'))}>
          Download my data
        </button>
        {!erasing ? (
          <button className="plain" onClick={() => setErasing(true)}>
            Delete my account…
          </button>
        ) : (
          <form onSubmit={erase}>
            <label>
              Confirm with your password
              <input type="password" required autoComplete="current-password" value={erasePassword} onChange={(e) => setErasePassword(e.target.value)} />
            </label>
            <button type="submit" data-testid="confirm-erase">
              Permanently delete my account
            </button>
          </form>
        )}
      </div>
      <div className="account-danger">
        <h3 className="eyebrow">Other devices</h3>
        <p>Lost a laptop or used a shared computer? End every session, including this one.</p>
        <button onClick={() => void everywhere()} data-testid="logout-all">
          Sign out everywhere
        </button>
      </div>
    </section>
  )
}
