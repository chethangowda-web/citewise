import { useState } from 'react'
import { askAPerson, sendFeedback } from '../api'
import type { ChatResponse } from '../types'

const REASONS = ['Wrong', 'Out of date', 'Missing information', 'Unclear']

/** Under an answer: was it helpful, and if the assistant could not help, ask a person. */
export function AnswerActions({ res }: { res: ChatResponse }) {
  const id = res.message_id
  const [rating, setRating] = useState<'up' | 'down' | null>(res.feedback === 1 ? 'up' : res.feedback === -1 ? 'down' : null)
  const [reasonSent, setReasonSent] = useState(false)
  const [askOpen, setAskOpen] = useState(false)
  const [note, setNote] = useState('')
  const [sent, setSent] = useState(false)
  const [error, setError] = useState('')
  if (!id || res.outcome === 'blocked') return null

  const rate = async (next: 'up' | 'down') => {
    const value = rating === next ? 'none' : next // pressing it again takes the vote back
    setError('')
    try {
      await sendFeedback(id, value)
      setRating(value === 'none' ? null : next)
      setReasonSent(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save that')
    }
  }
  const sendReason = async (text: string) => {
    setError('')
    try {
      await sendFeedback(id, 'down', text)
      setReasonSent(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save that')
    }
  }
  const ask = async () => {
    setError('')
    try {
      await askAPerson(id, note)
      setSent(true)
      setAskOpen(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not send your question')
    }
  }

  const canAsk = res.outcome === 'refused' || rating === 'down'

  return (
    <div className="actions" onClick={(e) => e.stopPropagation()}>
      <span className="actions-q">Was this helpful?</span>
      <button className={rating === 'up' ? 'on' : ''} aria-pressed={rating === 'up'} aria-label="Helpful" onClick={() => void rate('up')}>
        👍
      </button>
      <button className={rating === 'down' ? 'on' : ''} aria-pressed={rating === 'down'} aria-label="Not helpful" onClick={() => void rate('down')}>
        👎
      </button>
      {canAsk && !sent && (
        <button className="ask-person" onClick={() => setAskOpen((o) => !o)} aria-expanded={askOpen}>
          Ask a person
        </button>
      )}
      {sent && (
        <span className="actions-note" role="status" data-testid="escalated">
          Sent to the team. You will find their reply under Account.
        </span>
      )}

      {rating === 'down' && !reasonSent && (
        <div className="reasons" data-testid="reasons">
          <span>What went wrong?</span>
          {REASONS.map((r) => (
            <button key={r} onClick={() => void sendReason(r)}>
              {r}
            </button>
          ))}
        </div>
      )}
      {reasonSent && (
        <span className="actions-note" role="status">
          Thank you. That helps us fix it.
        </span>
      )}

      {askOpen && (
        <div className="ask-form">
          <textarea
            aria-label="Add a note for the team (optional)"
            placeholder="Anything the team should know? (optional)"
            maxLength={1000}
            rows={2}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <button onClick={() => void ask()}>Send to the team</button>
        </div>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}
