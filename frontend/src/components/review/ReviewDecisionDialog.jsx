import { AlertTriangle, X } from 'lucide-react'
import { useState } from 'react'
import ErrorState from '../common/ErrorState'
import ValueDisplay from '../intelligence/ValueDisplay'

function initialCorrection(value) {
  if (value !== null && typeof value === 'object') return JSON.stringify(value, null, 2)
  if (typeof value === 'boolean') return String(value)
  return value == null ? '' : String(value)
}

function parseCorrection(text, proposedValue) {
  if (Array.isArray(proposedValue) || (proposedValue !== null && typeof proposedValue === 'object')) {
    return JSON.parse(text)
  }
  if (typeof proposedValue === 'number') {
    const parsed = Number(text)
    if (!Number.isFinite(parsed)) throw new Error('Enter a valid numeric corrected value.')
    return parsed
  }
  if (typeof proposedValue === 'boolean') return text === 'true'
  return text
}

export default function ReviewDecisionDialog({
  item,
  decision,
  label,
  pending,
  error,
  onCancel,
  onSubmit,
}) {
  const [comments, setComments] = useState('')
  const [includeCorrection, setIncludeCorrection] = useState(false)
  const [correction, setCorrection] = useState(initialCorrection(item.proposed_value))
  const [validationError, setValidationError] = useState(null)
  const commentsRequired = decision !== 'approve' || includeCorrection
  const structured =
    Array.isArray(item.proposed_value) ||
    (item.proposed_value !== null && typeof item.proposed_value === 'object')

  function submit(event) {
    event.preventDefault()
    if (commentsRequired && !comments.trim()) {
      setValidationError('Reviewer comments are required for this decision.')
      return
    }
    try {
      onSubmit({
        comments: comments.trim() || null,
        corrected_value: includeCorrection
          ? parseCorrection(correction, item.proposed_value)
          : null,
      })
      setValidationError(null)
    } catch (parseError) {
      setValidationError(parseError.message)
    }
  }

  return (
    <div className="dialog-backdrop" role="presentation">
      <section
        className="review-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="decision-dialog-title"
      >
        <div className="review-dialog-heading">
          <div>
            <span className="eyebrow">Confirm consequential action</span>
            <h2 id="decision-dialog-title">{label}</h2>
          </div>
          <button className="icon-button" onClick={onCancel} disabled={pending} aria-label="Cancel decision">
            <X size={19} />
          </button>
        </div>
        <div className="decision-warning">
          <AlertTriangle size={17} />
          This decision applies only to this review finding, not to the entire bid or award.
        </div>
        <div className="review-proposal">
          <span>Original model-proposed value</span>
          <ValueDisplay value={item.proposed_value} label="Original proposed value" />
        </div>
        <form onSubmit={submit} className="decision-form">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={includeCorrection}
              onChange={(event) => setIncludeCorrection(event.target.checked)}
              disabled={pending}
            />
            Supply a human-corrected value
          </label>
          {includeCorrection && (
            <label>
              Human-corrected value
              {typeof item.proposed_value === 'boolean' ? (
                <select value={correction} onChange={(event) => setCorrection(event.target.value)}>
                  <option value="true">Yes</option>
                  <option value="false">No</option>
                </select>
              ) : typeof item.proposed_value === 'number' ? (
                <input
                  type="number"
                  value={correction}
                  onChange={(event) => setCorrection(event.target.value)}
                />
              ) : (
                <textarea
                  rows={structured ? 6 : 3}
                  value={correction}
                  onChange={(event) => setCorrection(event.target.value)}
                  aria-describedby={structured ? 'json-help' : undefined}
                />
              )}
              {structured && <small id="json-help">Enter valid JSON matching the proposed value structure.</small>}
            </label>
          )}
          <label>
            Reviewer comments {commentsRequired ? '(required)' : '(optional)'}
            <textarea
              rows="4"
              value={comments}
              onChange={(event) => setComments(event.target.value)}
              maxLength="4000"
              disabled={pending}
            />
          </label>
          {validationError && <p className="config-error" role="alert">{validationError}</p>}
          {error && <ErrorState title="Decision was not accepted" message={error.message} />}
          <div className="dialog-actions">
            <button className="button button-secondary" type="button" onClick={onCancel} disabled={pending}>
              Cancel
            </button>
            <button className="button button-primary" type="submit" disabled={pending}>
              {pending ? 'Submitting decision…' : `Confirm ${label.toLowerCase()}`}
            </button>
          </div>
        </form>
      </section>
    </div>
  )
}
