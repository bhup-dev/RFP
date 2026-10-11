import { Filter, RotateCcw, ShieldAlert } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import EmptyState from '../components/common/EmptyState'
import ErrorState from '../components/common/ErrorState'
import LoadingState from '../components/common/LoadingState'
import PageContainer from '../components/layout/PageContainer'
import ReviewerSession from '../components/review/ReviewerSession'
import ReviewQueueItem from '../components/review/ReviewQueueItem'
import {
  REVIEW_CATEGORIES,
  REVIEW_SEVERITIES,
  REVIEW_STATUSES,
} from '../constants/review'
import { useReviewAuth } from '../context/useReviewAuth'
import { listReviews } from '../services/reviewService'

export default function ReviewsPage() {
  const { auth } = useReviewAuth()
  const [filters, setFilters] = useState({ status: '', bid_id: '', category: '', severity: '' })
  const [bidDraft, setBidDraft] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState({ loading: false, data: null, error: null })

  useEffect(() => {
    if (!auth) return undefined
    const controller = new AbortController()
    // oxlint-disable-next-line react/set-state-in-effect -- marks the new authenticated request as loading
    setState({ loading: true, data: null, error: null })
    listReviews(
      { status: filters.status, bid_id: filters.bid_id },
      auth,
      controller.signal,
    )
      .then((data) => setState({ loading: false, data, error: null }))
      .catch((error) => {
        if (error.name !== 'AbortError') setState({ loading: false, data: null, error })
      })
    return () => controller.abort()
  }, [auth, filters.bid_id, filters.status, attempt])

  const visibleItems = useMemo(
    () =>
      (state.data || []).filter(
        (item) =>
          (!filters.category || item.category === filters.category) &&
          (!filters.severity || item.severity === filters.severity),
      ),
    [filters.category, filters.severity, state.data],
  )

  function clearFilters() {
    setBidDraft('')
    setFilters({ status: '', bid_id: '', category: '', severity: '' })
  }

  const errorTitle =
    state.error?.status === 401 || state.error?.status === 403
      ? 'Reviewer authorization failed'
      : state.error?.status === 503
        ? 'Review API unavailable'
        : 'Unable to load review queue'

  return (
    <PageContainer
      title="Human Review"
      description="Inspect evidence and submit authoritative decisions for uncertain workflow findings."
    >
      <ReviewerSession />
      {auth && (
        <>
          <section className="review-auth-limitation">
            <ShieldAlert size={18} />
            <span>
              The backend validates the shared API token but does not authenticate individual users
              or enforce reviewer roles.
            </span>
          </section>
          <section className="panel review-queue-panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">Persisted review items</span>
                <h2>Review queue</h2>
              </div>
              <Filter size={21} />
            </div>
            <div className="review-filters">
              <label>
                Status
                <select
                  value={filters.status}
                  onChange={(event) => setFilters((current) => ({ ...current, status: event.target.value }))}
                >
                  <option value="">All statuses</option>
                  {REVIEW_STATUSES.map((status) => (
                    <option key={status} value={status}>{status.replaceAll('_', ' ')}</option>
                  ))}
                </select>
              </label>
              <label>
                Category
                <select
                  value={filters.category}
                  onChange={(event) => setFilters((current) => ({ ...current, category: event.target.value }))}
                >
                  <option value="">All categories</option>
                  {REVIEW_CATEGORIES.map((category) => (
                    <option key={category} value={category}>{category.replaceAll('_', ' ')}</option>
                  ))}
                </select>
              </label>
              <label>
                Severity
                <select
                  value={filters.severity}
                  onChange={(event) => setFilters((current) => ({ ...current, severity: event.target.value }))}
                >
                  <option value="">All severities</option>
                  {REVIEW_SEVERITIES.map((severity) => (
                    <option key={severity} value={severity}>{severity}</option>
                  ))}
                </select>
              </label>
              <form
                onSubmit={(event) => {
                  event.preventDefault()
                  setFilters((current) => ({ ...current, bid_id: bidDraft.trim() }))
                }}
              >
                <label htmlFor="review-bid-filter">Bid UUID</label>
                <div>
                  <input id="review-bid-filter" value={bidDraft} onChange={(event) => setBidDraft(event.target.value)} />
                  <button className="button button-secondary" type="submit">Apply</button>
                </div>
              </form>
              {(filters.status || filters.bid_id || filters.category || filters.severity) && (
                <button className="button button-secondary" type="button" onClick={clearFilters}>
                  <RotateCcw size={14} /> Clear filters
                </button>
              )}
            </div>
            {state.loading && <LoadingState label="Loading protected review queue" />}
            {state.error && (
              <ErrorState
                title={errorTitle}
                message={state.error.message}
                onRetry={() => setAttempt((value) => value + 1)}
              />
            )}
            {!state.loading && !state.error && state.data && (
              <>
                <p className="filter-count">{visibleItems.length} review item(s) shown</p>
                {visibleItems.length > 0 ? (
                  <div className="review-queue">
                    {visibleItems.map((item) => (
                      <ReviewQueueItem item={item} key={item.review_item_id} />
                    ))}
                  </div>
                ) : (
                  <EmptyState
                    title={state.data.length ? 'No reviews match these filters' : 'Review queue is empty'}
                    description={
                      state.data.length
                        ? 'Clear or adjust filters without changing the persisted queue.'
                        : 'The backend returned no review items for this request.'
                    }
                  />
                )}
              </>
            )}
          </section>
        </>
      )}
    </PageContainer>
  )
}
