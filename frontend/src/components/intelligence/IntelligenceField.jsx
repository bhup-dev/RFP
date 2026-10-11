import { AlertCircle, ChevronDown, ChevronUp, Quote } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { profileFieldAnchor, REVIEW_STATUSES } from '../../constants/profileFields'
import StatusBadge from '../common/StatusBadge'
import CitationList from './CitationList'
import ValueDisplay from './ValueDisplay'

const STATUS_HELP = {
  validated: 'Evidence checks support this extracted value.',
  partially_supported: 'Some, but not all, aspects are supported by evidence.',
  unsupported: 'The backend did not find sufficient supporting evidence.',
  conflicting: 'Source evidence contains unresolved conflicting statements.',
  not_found: 'No value was identified in the available source evidence.',
  needs_review: 'A human reviewer should verify this field.',
  unvalidated: 'The field has not completed evidence validation.',
}

export default function IntelligenceField({ name, field, reviews = [] }) {
  const [evidenceOpen, setEvidenceOpen] = useState(false)
  const status = field?.validation_status || 'unvalidated'
  const supporting = field?.supporting_citations || []
  const contradicting = field?.contradicting_citations || []
  const evidenceCount = supporting.length + contradicting.length
  const requiresReview = REVIEW_STATUSES.has(status)
  const wide = ['Bid Summary', 'Product Specification', 'Additional Documentation Required'].includes(name)

  return (
    <article id={profileFieldAnchor(name)} className={`intelligence-field ${wide ? 'field-wide' : ''}`} data-status={status}>
      <div className="intelligence-field-heading">
        <div>
          <h3>{name}</h3>
          <p className="status-help">{STATUS_HELP[status] || 'Status reported by the backend.'}</p>
        </div>
        <StatusBadge status={status} />
      </div>

      <div className="intelligence-value">
        <ValueDisplay value={field?.value} label={name} />
      </div>

      <div className="field-facts">
        {field?.confidence_score != null && (
          <span title="Backend-provided confidence heuristic">
            Confidence {Math.round(field.confidence_score * 100)}%
          </span>
        )}
        <span>{supporting.length} supporting</span>
        <span>{contradicting.length} contradicting</span>
        {requiresReview && (
          <span className="review-indicator"><AlertCircle size={14} /> Review required</span>
        )}
      </div>

      {reviews.length > 0 && (
        <div className="field-review-links">
          <strong>Human review</strong>
          {reviews.map((review) => (
            <Link key={review.review_item_id} to={`/reviews/${review.review_item_id}`}>
              {review.status.replaceAll('_', ' ')} · {review.category.replaceAll('_', ' ')}
            </Link>
          ))}
        </div>
      )}

      {(field?.extraction_notes || field?.review_recommendation) && (
        <div className="field-guidance">
          {field.extraction_notes && <p><strong>Extraction note:</strong> {field.extraction_notes}</p>}
          {field.review_recommendation && (
            <p><strong>Recommended action:</strong> {field.review_recommendation}</p>
          )}
        </div>
      )}

      <button
        type="button"
        className="evidence-toggle"
        aria-expanded={evidenceOpen}
        onClick={() => setEvidenceOpen((open) => !open)}
      >
        <Quote size={16} />
        {evidenceOpen ? 'Close evidence' : `Inspect evidence (${evidenceCount})`}
        {evidenceOpen ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
      </button>

      {evidenceOpen && (
        <div className="field-evidence-panel">
          <section>
            <h4>Supporting evidence</h4>
            <CitationList citations={supporting} role="supporting" />
          </section>
          {(contradicting.length > 0 || status === 'conflicting') && (
            <section>
              <h4>Contradicting evidence</h4>
              <CitationList citations={contradicting} role="contradicting" />
            </section>
          )}
        </div>
      )}
    </article>
  )
}
