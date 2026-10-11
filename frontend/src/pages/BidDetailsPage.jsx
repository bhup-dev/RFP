import { AlertTriangle, ArrowLeft, FileSearch, GitCompare, MessageSquareText, Search, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import BidChat from '../components/bids/BidChat'
import BidOverview from '../components/bids/BidOverview'
import AddendumChanges from '../components/intelligence/AddendumChanges'
import IntelligenceField from '../components/intelligence/IntelligenceField'
import EmptyState from '../components/common/EmptyState'
import ErrorState from '../components/common/ErrorState'
import LoadingState from '../components/common/LoadingState'
import StatusBadge from '../components/common/StatusBadge'
import PageContainer from '../components/layout/PageContainer'
import { PROFILE_FIELD_NAMES, REVIEW_STATUSES } from '../constants/profileFields'
import { useReviewAuth } from '../context/useReviewAuth'
import { getBid, getIntelligenceProfile } from '../services/bidService'
import { listReviews } from '../services/reviewService'
import { formatTimestamp } from '../utils/format'

function metadataRows(metadata) {
  return [
    ['Bid identifier', metadata.bid_identifier],
    ['Solicitation number', metadata.solicitation_number],
    ['Generated', formatTimestamp(metadata.generated_at)],
    ['Extraction run', metadata.extraction_run_id],
    ['Validation run', metadata.validation_run_id],
    ['Addendum run', metadata.addendum_analysis_run_id],
  ]
}

export default function BidDetailsPage() {
  const { bidId } = useParams()
  const { auth } = useReviewAuth()
  const [state, setState] = useState({
    loading: true,
    data: null,
    error: null,
    requestBid: null,
  })
  const [attempt, setAttempt] = useState(0)
  const [view, setView] = useState('profile')
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('all')
  const [reviewOnly, setReviewOnly] = useState(false)
  const [fieldReviews, setFieldReviews] = useState({ data: [], error: null })
  const [bidState, setBidState] = useState({ data: null, error: null, requestBid: null })
  const chatRef = useRef(null)

  useEffect(() => {
    const controller = new AbortController()
    getIntelligenceProfile(bidId, { signal: controller.signal })
      .then((data) => setState({ loading: false, data, error: null, requestBid: bidId }))
      .catch((error) => {
        if (error.name !== 'AbortError') {
          setState({ loading: false, data: null, error, requestBid: bidId })
        }
      })
    return () => controller.abort()
  }, [bidId, attempt])

  useEffect(() => {
    const controller = new AbortController()
    getBid(bidId, controller.signal)
      .then((data) => setBidState({ data, error: null, requestBid: bidId }))
      .catch((error) => {
        if (error.name !== 'AbortError') setBidState({ data: null, error, requestBid: bidId })
      })
    return () => controller.abort()
  }, [bidId, attempt])

  function reloadAll() {
    setState({ loading: true, data: null, error: null, requestBid: bidId })
    setAttempt((value) => value + 1)
  }

  useEffect(() => {
    if (!auth) return undefined
    const controller = new AbortController()
    listReviews({ bid_id: bidId }, auth, controller.signal)
      .then((data) => setFieldReviews({ data, error: null }))
      .catch((error) => {
        if (error.name !== 'AbortError') setFieldReviews({ data: [], error })
      })
    return () => controller.abort()
  }, [auth, bidId])

  const normalizedFields = useMemo(
    () =>
      PROFILE_FIELD_NAMES.map((name) => [
        name,
        state.data?.fields?.[name] || {
          value: null,
          validation_status: 'unvalidated',
          supporting_citations: [],
          contradicting_citations: [],
          addendum_changes: [],
          review_recommendation: 'Field data was not returned by the backend.',
        },
      ]),
    [state.data],
  )
  const statuses = useMemo(
    () => [...new Set(normalizedFields.map(([, field]) => field.validation_status))].sort(),
    [normalizedFields],
  )
  const filteredFields = useMemo(() => {
    const term = search.trim().toLowerCase()
    return normalizedFields.filter(([name, field]) => {
      const matchesName = !term || name.toLowerCase().includes(term)
      const matchesStatus = status === 'all' || field.validation_status === status
      const matchesReview = !reviewOnly || REVIEW_STATUSES.has(field.validation_status)
      return matchesName && matchesStatus && matchesReview
    })
  }, [normalizedFields, reviewOnly, search, status])
  const profileNotReady =
    state.error?.status === 404 && bidState.requestBid === bidId && Boolean(bidState.data)
  const bidLoaded = bidState.requestBid === bidId && Boolean(bidState.data)
  const chatPanel = bidLoaded && (
    <BidChat
      key={`chat-${bidId}`}
      ref={chatRef}
      bidId={bidId}
      indexedCount={bidState.data.indexed_document_count ?? null}
    />
  )
  const addendumChanges = useMemo(
    () =>
      normalizedFields.flatMap(([fieldName, field]) =>
        (field.addendum_changes || []).map((change) => ({ fieldName, change })),
      ),
    [normalizedFields],
  )

  return (
    <PageContainer
      title="Bid intelligence profile"
      description={`Evidence-grounded extraction for bid ${bidId}`}
      actions={
        <>
          {bidLoaded && (
            <button className="button button-primary" type="button" onClick={() => chatRef.current?.focus()}>
              <MessageSquareText size={17} /> Ask questions
            </button>
          )}
          <Link to="/bids" className="button button-secondary">
            <ArrowLeft size={17} /> Back to bids
          </Link>
        </>
      }
    >
      {(state.loading || state.requestBid !== bidId) && (
        <LoadingState label="Loading intelligence profile" />
      )}
      {state.requestBid === bidId && state.error && profileNotReady && (
        <EmptyState
          title="Not processed yet"
          description={
            bidState.data.document_count
              ? 'This bid has documents but no persisted intelligence profile. Start processing below; the profile appears when analysis finishes.'
              : 'Upload the solicitation and any addenda below, then start processing.'
          }
        />
      )}
      {state.requestBid === bidId && state.error && !profileNotReady && (
        <ErrorState
          title={state.error.status === 404 ? 'Profile unavailable' : 'Unable to load profile'}
          message={state.error.message}
          onRetry={reloadAll}
        />
      )}
      {state.requestBid === bidId && state.data && (
        <section className="profile-summary panel">
          <div>
            <span className="eyebrow">Profile status</span>
            <h2>{state.data.fields?.Title?.value || state.data.metadata.bid_identifier}</h2>
            <p className="muted">
              {state.data.metadata.source_document_count} registered source documents ·
              Profile v{state.data.metadata.profile_version}
            </p>
          </div>
          <div className="profile-status-group">
            <StatusBadge status={state.data.metadata.status} />
            {state.data.metadata.review_required && (
              <span className="review-indicator"><AlertTriangle size={14} /> Human review required</span>
            )}
          </div>
        </section>
      )}

      {chatPanel}

      {state.requestBid === bidId && state.data && (
        <>
          {(state.data.metadata.warnings?.length > 0 ||
            state.data.metadata.unresolved_issues?.length > 0) && (
            <section className="profile-alerts" role="alert">
              <AlertTriangle size={20} />
              <div>
                <strong>Unresolved profile issues</strong>
                <ul>
                  {[...(state.data.metadata.warnings || []), ...(state.data.metadata.unresolved_issues || [])]
                    .map((item) => <li key={item}>{item}</li>)}
                </ul>
              </div>
            </section>
          )}

          <section className="panel profile-metadata">
            <div className="panel-heading">
              <div><span className="eyebrow">Lineage</span><h2>Profile metadata</h2></div>
            </div>
            <dl className="metadata-grid">
              {metadataRows(state.data.metadata).map(([label, value]) => (
                <div key={label}><dt>{label}</dt><dd>{value || 'Unavailable'}</dd></div>
              ))}
            </dl>
          </section>

          <nav className="profile-tabs" aria-label="Intelligence profile sections">
            <button className={view === 'profile' ? 'active' : ''} onClick={() => setView('profile')}>
              <FileSearch size={17} /> Profile & evidence
            </button>
            <button className={view === 'addenda' ? 'active' : ''} onClick={() => setView('addenda')}>
              <GitCompare size={17} /> Addendum changes
              <span>{addendumChanges.length}</span>
            </button>
          </nav>

          {view === 'profile' && (
            <section className="panel">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">20 required fields</span>
                  <h2>Structured extraction</h2>
                </div>
                <FileSearch size={22} />
              </div>
              <div className="profile-filters">
                <label>
                  <span>Search field names</span>
                  <div className="filter-input"><Search size={16} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Due date, product…" /></div>
                </label>
                <label>
                  <span>Validation status</span>
                  <select value={status} onChange={(event) => setStatus(event.target.value)}>
                    <option value="all">All statuses</option>
                    {statuses.map((item) => <option value={item} key={item}>{item.replaceAll('_', ' ')}</option>)}
                  </select>
                </label>
                <label className="review-filter">
                  <input type="checkbox" checked={reviewOnly} onChange={(event) => setReviewOnly(event.target.checked)} />
                  Review required only
                </label>
                {(search || status !== 'all' || reviewOnly) && (
                  <button className="button button-secondary" onClick={() => { setSearch(''); setStatus('all'); setReviewOnly(false) }}>
                    <X size={15} /> Clear
                  </button>
                )}
              </div>
              <p className="filter-count">{filteredFields.length} of 20 fields shown</p>
              {auth && fieldReviews.error && (
                <p className="review-integration-warning">
                  Human-review links are unavailable: {fieldReviews.error.message}
                </p>
              )}
              {filteredFields.length ? (
                <div className="intelligence-grid">
                  {filteredFields.map(([name, field]) => (
                    <IntelligenceField
                      name={name}
                      field={field}
                      reviews={auth ? fieldReviews.data.filter((review) => review.field_name === name) : []}
                      key={name}
                    />
                  ))}
                </div>
              ) : (
                <EmptyState
                  title="No fields match the current filters"
                  description="Clear the filters to restore all 20 required fields."
                />
              )}
            </section>
          )}

          {view === 'addenda' && (
            <section className="panel">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">Source-preserving reconciliation</span>
                  <h2>Addendum changes and conflicts</h2>
                </div>
                <GitCompare size={22} />
              </div>
              <p className="muted">
                Original and amended values remain separate. This interface does not decide which
                statement controls.
              </p>
              <AddendumChanges changes={addendumChanges} />
            </section>
          )}

        </>
      )}
      {bidState.requestBid === bidId && (
        <BidOverview
          key={bidId}
          bidId={bidId}
          bid={bidState.data}
          error={bidState.error}
          onChanged={reloadAll}
        />
      )}
    </PageContainer>
  )
}
