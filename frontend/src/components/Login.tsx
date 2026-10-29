import { useState } from 'react'
import { setToken, signIn } from '../api'
import type { AuthUser } from '../types'

interface Props {
  allowSignup: boolean
  onSignedIn: (user: AuthUser) => void
  notice?: string
}

export function Login({ allowSignup, onSignedIn, notice }: Props) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const { token, user } = await signIn(mode, email, password)
      setToken(token)
      onSignedIn(user)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not sign in')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-wrap">
      <form className="login" onSubmit={submit} aria-label={mode === 'login' ? 'Sign in' : 'Create account'}>
        <h1>Citewise</h1>
        <p>{mode === 'login' ? 'Sign in to open the archive.' : 'Create an account. Your uploads stay private to you.'}</p>
        {notice && (
          <p className="notice" role="status">
            {notice}
          </p>
        )}
        <label>
          Email
          <input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
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
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" disabled={busy}>
          {busy ? 'One moment…' : mode === 'login' ? 'Sign in' : 'Create account'}
        </button>
        {allowSignup && (
          <button
            type="button"
            className="link"
            onClick={() => {
              setMode(mode === 'login' ? 'register' : 'login')
              setError('')
            }}
          >
            {mode === 'login' ? 'New here? Create an account' : 'Have an account? Sign in'}
          </button>
        )}
      </form>
    </div>
  )
}
