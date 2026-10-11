import { AlertTriangle } from 'lucide-react'

export default function ErrorState({ title = 'Something went wrong', message, onRetry }) {
  return (
    <div className="state-card state-error" role="alert">
      <AlertTriangle size={24} />
      <div>
        <strong>{title}</strong>
        <p>{message || 'The request could not be completed.'}</p>
        {onRetry && (
          <button className="button button-secondary" onClick={onRetry}>
            Try again
          </button>
        )}
      </div>
    </div>
  )
}
