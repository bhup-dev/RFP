import { AlertTriangle } from 'lucide-react'
import { CATEGORY_HELP } from '../../constants/comparison'
import StatusBadge from '../common/StatusBadge'
import ComparisonCell from './ComparisonCell'

const DIMENSION_LABELS = {
  scope: 'Bid and scope',
  products: 'Products and specifications',
  commercial_terms: 'Commercial terms',
  compliance: 'Compliance and documentation',
  addenda: 'Addendum changes',
}

export default function ComparisonTable({ report, rows }) {
  const references = new Map(
    (report.profile_references || []).map((reference) => [reference.bid_id, reference]),
  )

  return (
    <div className="comparison-table-scroll" tabIndex="0" aria-label="Scrollable bid comparison">
      <table className="comparison-table">
        <thead>
          <tr>
            <th scope="col">Comparison field</th>
            {report.bid_ids.map((bidId) => {
              const reference = references.get(bidId)
              return (
                <th scope="col" key={bidId}>
                  <strong>{reference?.bid_identifier || 'Bid'}</strong>
                  <code>{bidId}</code>
                  {reference && <StatusBadge status={reference.profile_status} />}
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.dimension}-${row.attribute}`}>
              <th scope="row">
                {(index === 0 || rows[index - 1].dimension !== row.dimension) && (
                  <span className="dimension-label">
                    {DIMENSION_LABELS[row.dimension] || row.dimension}
                  </span>
                )}
                <strong>{row.attribute}</strong>
                <StatusBadge status={row.classification} />
                <span className="classification-help">
                  {CATEGORY_HELP[row.classification] || row.explanation}
                </span>
                <p>{row.explanation}</p>
                {row.confidence != null && (
                  <span className="row-confidence" title="Backend-provided comparison confidence heuristic">
                    Confidence {Math.round(row.confidence * 100)}%
                  </span>
                )}
                {row.requires_human_review && (
                  <span className="review-indicator">
                    <AlertTriangle size={13} /> Human review required
                  </span>
                )}
                {row.review_recommendation && (
                  <small>{row.review_recommendation}</small>
                )}
              </th>
              {report.bid_ids.map((bidId) => (
                <ComparisonCell
                  key={`${row.attribute}-${bidId}`}
                  bidId={bidId}
                  profile={references.get(bidId)}
                  value={(row.values || []).find((item) => item.bid_id === bidId)}
                />
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
