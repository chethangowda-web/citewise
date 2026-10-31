import { useState } from 'react'
import { changePassword, logoutAll } from '../api'

export function AccountPanel({ email, onClose, onSignedOut }: { email: string; onClose: () => void; onSignedOut: () => void }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

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

  return (
    <section className="account-panel" aria-label="Your account">
      <header>
        <strong>{email}</strong>
        <button onClick={onClose} aria-label="Close account panel">
          ×
        </button>
      </header>
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
