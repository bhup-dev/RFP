import { AlertTriangle, GitCompare } from 'lucide-react'
import EmptyState from '../common/EmptyState'
import StatusBadge from '../common/StatusBadge'
import CitationList from './CitationList'
import ValueDisplay from './ValueDisplay'

export default function AddendumChanges({ changes = [] }) {
  if (!changes.length) {
    return (
      <EmptyState
        title="No field-level addendum changes in this profile"
        description="No persisted changes were attached to the profile fields. Historical addendum runs cannot be fetched because the backend has no read endpoint for them."
      />
    )
  }

  return (
    <div className="addendum-list">
      {changes.map(({ fieldName, change }) => {
        const conflict = ['conflict', 'conflicting', 'ambiguous'].includes(change.classification)
        return (
          <article className={`addendum-card ${conflict ? 'addendum-conflict' : ''}`} key={change.finding_id}>
            <div className="addendum-heading">
              <div>
                <span className="eyebrow">{fieldName}</span>
                <h3>{change.topic}</h3>
              </div>
              <StatusBadge status={change.classification} />
            </div>
            {conflict && (
              <div className="conflict-callout">
                <AlertTriangle size={17} />
                This finding remains unresolved and requires review.
              </div>
            )}
            <div className="change-values">
              <section>
                <span>Original RFP</span>
                <ValueDisplay value={change.original_value} label="Original value" />
              </section>
              <GitCompare size={20} aria-hidden="true" />
              <section>
                <span>Addendum statement</span>
                <ValueDisplay value={change.amended_value} label="Amended value" />
              </section>
            </div>
            {change.explanation && <p className="change-explanation">{change.explanation}</p>}
            <div className="change-sources">
              <section>
                <h4>Original sources</h4>
                <CitationList citations={change.original_sources} role="original" />
              </section>
              <section>
                <h4>Addendum sources</h4>
                <CitationList citations={change.addendum_sources} role="addendum" />
              </section>
            </div>
          </article>
        )
      })}
    </div>
  )
}
