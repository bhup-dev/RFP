import { KeyRound, LogOut } from 'lucide-react'
import { useState } from 'react'
import { useReviewAuth } from '../../context/useReviewAuth'

export default function ReviewerSession() {
  const { auth, setCredentials, clearCredentials } = useReviewAuth()
  const [reviewerId, setReviewerId] = useState('')
  const [token, setToken] = useState('')

  if (auth) {
    return (
      <div className="reviewer-session active">
        <div>
          <span>Reviewer session</span>
          <strong>{auth.reviewerId}</strong>
        </div>
        <button className="button button-secondary" type="button" onClick={clearCredentials}>
          <LogOut size={15} /> End session
        </button>
      </div>
    )
  }

  return (
    <section className="panel reviewer-login">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">Protected review API</span>
          <h2>Start reviewer session</h2>
        </div>
        <KeyRound size={22} />
      </div>
      <p className="muted">
        Enter credentials issued by the backend administrator. They remain only in application
        memory and are cleared on refresh.
      </p>
      <form
        className="reviewer-login-form"
        onSubmit={(event) => {
          event.preventDefault()
          setCredentials(token, reviewerId)
          setToken('')
        }}
      >
        <label>
          Reviewer ID
          <input value={reviewerId} onChange={(event) => setReviewerId(event.target.value)} required />
        </label>
        <label>
          Review API token
          <input
            type="password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            autoComplete="off"
            required
          />
        </label>
        <button className="button button-primary" type="submit">Connect to review queue</button>
      </form>
      <p className="security-note">
        The backend uses a shared token and trusts the supplied reviewer ID. It does not implement
        per-user authentication or role authorization.
      </p>
    </section>
  )
}
