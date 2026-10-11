import { ExternalLink, Plus, Trash2 } from 'lucide-react'
import { Link } from 'react-router-dom'
import ErrorState from '../common/ErrorState'
import StatusBadge from '../common/StatusBadge'

export default function BidSelector({
  value,
  onValueChange,
  onAdd,
  onRemove,
  selectedBids,
  availableBids = [],
  availableError = null,
  loading,
  error,
}) {
  const selectable = availableBids.filter(
    (bid) => !selectedBids.some((selected) => selected.id === bid.id),
  )
  return (
    <section className="panel comparison-selector">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">Bid selection</span>
          <h2>Select at least two bids</h2>
        </div>
        <span className="selection-count">{selectedBids.length} selected</span>
      </div>
      <p className="integration-note">
        Choose stored bids or paste a bid ID. Each bid is verified by loading its persisted
        intelligence profile; bids that have not been processed yet cannot be compared.
      </p>
      {availableError && (
        <p className="review-integration-warning">
          The bid list could not be loaded ({availableError.message}); paste bid IDs instead.
        </p>
      )}
      <form className="bid-add-form" onSubmit={onAdd}>
        {selectable.length > 0 && (
          <>
            <label htmlFor="comparison-bid-pick">Stored bid</label>
            <select
              id="comparison-bid-pick"
              value={selectable.some((bid) => bid.id === value) ? value : ''}
              onChange={(event) => onValueChange(event.target.value)}
              disabled={loading}
            >
              <option value="">Choose a bid…</option>
              {selectable.map((bid) => (
                <option key={bid.id} value={bid.id}>
                  {bid.title ? `${bid.title} (${bid.bid_identifier})` : bid.bid_identifier}
                </option>
              ))}
            </select>
          </>
        )}
        <label htmlFor="comparison-bid-id">Bid UUID</label>
        <div>
          <input
            id="comparison-bid-id"
            value={value}
            onChange={(event) => onValueChange(event.target.value)}
            placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
            disabled={loading}
          />
          <button className="button button-secondary" type="submit" disabled={loading || !value.trim()}>
            <Plus size={16} /> {loading ? 'Checking…' : 'Add bid'}
          </button>
        </div>
      </form>
      {error && <ErrorState title="Unable to add bid" message={error.message} />}
      <div className="selected-bids" aria-label="Selected bids">
        {selectedBids.length === 0 && (
          <p className="muted">No bids selected. Comparison does not run automatically.</p>
        )}
        {selectedBids.map((bid) => (
          <article key={bid.id}>
            <div>
              <strong>{bid.title || bid.metadata.bid_identifier}</strong>
              <span>{bid.metadata.bid_identifier} · {bid.id}</span>
              <span>
                Solicitation {bid.metadata.solicitation_number || 'unavailable'} ·
                {bid.metadata.source_document_count} sources
              </span>
            </div>
            <StatusBadge status={bid.metadata.status} />
            <Link
              to={`/bids/${bid.id}`}
              target="_blank"
              rel="noreferrer"
              className="icon-button"
              aria-label={`Open ${bid.metadata.bid_identifier} profile in a new tab`}
            >
              <ExternalLink size={16} />
            </Link>
            <button
              type="button"
              className="icon-button remove-bid"
              aria-label={`Remove ${bid.metadata.bid_identifier}`}
              onClick={() => onRemove(bid.id)}
              disabled={loading}
            >
              <Trash2 size={16} />
            </button>
          </article>
        ))}
      </div>
    </section>
  )
}
