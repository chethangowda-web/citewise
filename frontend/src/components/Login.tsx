import { useState } from 'react'
import { forgotPassword, signIn } from '../api'
import type { AuthUser } from '../types'

interface Props {
  allowSignup: boolean
  onSignedIn: (user: AuthUser) => void
  notice?: string
}

export function Login({ allowSignup, onSignedIn, notice }: Props) {
  const [mode, setMode] = useState<'login' | 'register' | 'forgot'>('login')
  const [sent, setSent] = useState(false)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      if (mode === 'forgot') {
        await forgotPassword(email)
        setSent(true)
        return
      }
      const { user } = await signIn(mode, email, password)
      onSignedIn(user)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not sign in')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-wrap">
      <form className="login" onSubmit={submit} aria-label={mode === 'login' ? 'Sign in' : mode === 'register' ? 'Create account' : 'Reset password'}>
        <h1>Citewise</h1>
        <p>
          {mode === 'login' && 'Sign in to open the archive.'}
          {mode === 'register' && 'Create an account. Your uploads stay private to you.'}
          {mode === 'forgot' && 'Enter your email and we will send you a link to choose a new password.'}
        </p>
        {notice && (
          <p className="notice" role="status">
            {notice}
          </p>
        )}
        <label>
          Email
          <input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        {mode !== 'forgot' && (
        <label>
          Password
          <input
            type="password"
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            required
            minLength={mode === 'register' ? 8 : undefined}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        )}
        {mode === 'forgot' && sent && (
          <p className="notice" role="status" data-testid="forgot-sent">
            If an account exists for that address, a reset link is on its way. It works once and expires soon.
          </p>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" disabled={busy}>
          {busy ? 'One moment…' : mode === 'login' ? 'Sign in' : mode === 'register' ? 'Create account' : 'Send reset link'}
        </button>
        {mode === 'login' && (
          <button
            type="button"
            className="link"
            onClick={() => {
              setMode('forgot')
              setError('')
              setSent(false)
            }}
          >
            Forgot your password?
          </button>
        )}
        {(allowSignup || mode === 'forgot') && (
          <button
            type="button"
            className="link"
            onClick={() => {
              setMode(mode === 'login' && allowSignup ? 'register' : 'login')
              setError('')
            }}
          >
            {mode === 'login' ? 'New here? Create an account' : mode === 'register' ? 'Have an account? Sign in' : 'Back to sign in'}
          </button>
        )}
      </form>
    </div>
  )
}
