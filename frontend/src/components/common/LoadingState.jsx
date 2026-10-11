export default function LoadingState({ label = 'Loading…' }) {
  return (
    <div className="state-card" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <div>
        <strong>{label}</strong>
        <p>Please wait while the latest information is retrieved.</p>
      </div>
    </div>
  )
}
