import { ChevronDown, ChevronUp, ExternalLink, Quote } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import CitationList from '../intelligence/CitationList'
import ValueDisplay from '../intelligence/ValueDisplay'
import StatusBadge from '../common/StatusBadge'

export default function ComparisonCell({ bidId, profile, value }) {
  const [open, setOpen] = useState(false)

  if (!value) {
    return (
      <td className="comparison-cell">
        <span className="missing-value">No value returned for this bid</span>
        <Link className="profile-cell-link" to={`/bids/${bidId}`} target="_blank" rel="noreferrer">
          Open profile <ExternalLink size={12} />
        </Link>
      </td>
    )
  }

  const supporting = (value.supporting_citations || []).filter(
    (citation) => citation.bid_id === bidId,
  )
  const contradicting = (value.contradicting_citations || []).filter(
    (citation) => citation.bid_id === bidId,
  )
  const excluded =
    (value.supporting_citations || []).length + (value.contradicting_citations || []).length -
    supporting.length - contradicting.length
  const evidenceCount = supporting.length + contradicting.length

  return (
    <td className="comparison-cell">
      <div className="comparison-cell-heading">
        <StatusBadge status={value.field_status} />
        <Link
          className="profile-cell-link"
          to={`/bids/${bidId}`}
          target="_blank"
          rel="noreferrer"
          aria-label={`Open ${profile?.bid_identifier || bidId} profile in a new tab`}
        >
          Profile <ExternalLink size={12} />
        </Link>
      </div>
      <div className="comparison-value">
        <ValueDisplay value={value.original_value} label={`${profile?.bid_identifier || bidId} value`} />
      </div>
      {value.addendum_changes?.length > 0 && (
        <span className="cell-addendum">{value.addendum_changes.length} addendum change(s)</span>
      )}
      {excluded > 0 && (
        <p className="citation-integrity-warning">
          {excluded} citation(s) excluded because their bid identifier did not match.
        </p>
      )}
      <button
        className="cell-evidence-toggle"
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <Quote size={14} /> Evidence ({evidenceCount})
        {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
      </button>
      {open && (
        <div className="comparison-evidence">
          <h4>Supporting evidence</h4>
          <CitationList citations={supporting} role="supporting" />
          {(contradicting.length > 0 || value.field_status === 'conflicting') && (
            <>
              <h4>Contradicting evidence</h4>
              <CitationList citations={contradicting} role="contradicting" />
            </>
          )}
        </div>
      )}
    </td>
  )
}
