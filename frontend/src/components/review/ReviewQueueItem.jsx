import { ArrowRight, Clock3, ExternalLink } from 'lucide-react'
import { Link } from 'react-router-dom'
import StatusBadge from '../common/StatusBadge'
import ValueDisplay from '../intelligence/ValueDisplay'

export default function ReviewQueueItem({ item }) {
  const evidenceCount =
    (item.supporting_citations || []).length + (item.contradicting_citations || []).length

  return (
    <article className="review-queue-item">
      <div className="review-item-heading">
        <div>
          <span className="eyebrow">{item.operation_type.replaceAll('_', ' ')}</span>
          <h3>{item.field_name || item.item_reference}</h3>
        </div>
        <div className="review-item-statuses">
          <StatusBadge status={item.severity} />
          <StatusBadge status={item.status} />
        </div>
      </div>
      <p className="review-reason">{item.reason}</p>
      <div className="review-proposal">
        <span>Model-proposed value</span>
        <ValueDisplay value={item.proposed_value} label="Proposed value" />
      </div>
      <dl className="review-item-facts">
        <div><dt>Category</dt><dd>{item.category.replaceAll('_', ' ')}</dd></div>
        <div><dt>Source stage</dt><dd>{item.source_stage.replaceAll('_', ' ')}</dd></div>
        <div><dt>Validation</dt><dd>{item.validation_status?.replaceAll('_', ' ') || 'Unavailable'}</dd></div>
        <div><dt>Evidence</dt><dd>{evidenceCount} citation(s)</dd></div>
        {item.confidence_score != null && (
          <div><dt>Confidence</dt><dd>{Math.round(item.confidence_score * 100)}%</dd></div>
        )}
      </dl>
      <div className="review-item-footer">
        <span><Clock3 size={13} /> {new Date(item.created_at).toLocaleString()}</span>
        <Link to={`/bids/${item.bid_id}`} target="_blank" rel="noreferrer">
          Bid profile <ExternalLink size={12} />
        </Link>
        <Link className="button button-secondary" to={`/reviews/${item.review_item_id}`}>
          Open review <ArrowRight size={14} />
        </Link>
      </div>
    </article>
  )
}
