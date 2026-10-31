import { useState } from 'react'
import { resetPassword } from '../api'

/** Shown when someone opens the link from a password-reset email (`/?reset=<token>`). */
export function ResetPassword({ token, onDone }: { token: string; onDone: () => void }) {
  const [password, setPassword] = useState('')
  const [again, setAgain] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [finished, setFinished] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (password !== again) return setError('The two passwords do not match')
    setBusy(true)
    setError('')
    try {
      await resetPassword(token, password)
      setFinished(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not reset the password')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-wrap">
      <form className="login" onSubmit={submit} aria-label="Choose a new password">
        <h1>Citewise</h1>
        {finished ? (
          <>
            <p className="notice" role="status" data-testid="reset-done">
              Your password has been changed and every other session was signed out.
            </p>
            <button type="button" onClick={onDone}>
              Go to sign in
            </button>
          </>
        ) : (
          <>
            <p>Choose a new password (at least 8 characters).</p>
            <label>
              New password
              <input type="password" autoComplete="new-password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} />
            </label>
            <label>
              Repeat it
              <input type="password" autoComplete="new-password" required minLength={8} value={again} onChange={(e) => setAgain(e.target.value)} />
            </label>
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
            <button type="submit" disabled={busy}>
              {busy ? 'One moment…' : 'Change password'}
            </button>
            <button type="button" className="link" onClick={onDone}>
              Back to sign in
            </button>
          </>
        )}
      </form>
    </div>
  )
}
