import { ArrowLeft, ExternalLink, History, ShieldCheck, Workflow } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import EmptyState from '../components/common/EmptyState'
import ErrorState from '../components/common/ErrorState'
import LoadingState from '../components/common/LoadingState'
import StatusBadge from '../components/common/StatusBadge'
import CitationList from '../components/intelligence/CitationList'
import ValueDisplay from '../components/intelligence/ValueDisplay'
import PageContainer from '../components/layout/PageContainer'
import ReviewDecisionDialog from '../components/review/ReviewDecisionDialog'
import ReviewerSession from '../components/review/ReviewerSession'
import { REVIEW_DECISIONS } from '../constants/review'
import { profileFieldAnchor } from '../constants/profileFields'
import { useReviewAuth } from '../context/useReviewAuth'
import {
  getReview,
  getWorkflowReviewStatus,
  submitReviewDecision,
} from '../services/reviewService'

function createIdempotencyKey() {
  return crypto.randomUUID()
}

export default function ReviewDetailPage() {
  const { reviewId } = useParams()
  const { auth } = useReviewAuth()
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState({
    loading: false,
    item: null,
    workflow: null,
    error: null,
    workflowError: null,
  })
  const [confirmation, setConfirmation] = useState(null)
  const [decisionState, setDecisionState] = useState({
    pending: false,
    error: null,
    outcome: null,
    refreshError: null,
  })

  useEffect(() => {
    if (!auth) return undefined
    const controller = new AbortController()
    // oxlint-disable-next-line react/set-state-in-effect -- marks the new authenticated request as loading
    setState({ loading: true, item: null, workflow: null, error: null, workflowError: null })
    getReview(reviewId, auth, controller.signal)
      .then(async (item) => {
        setState({ loading: false, item, workflow: null, error: null, workflowError: null })
        try {
          const workflow = await getWorkflowReviewStatus(item.workflow_id, auth, controller.signal)
          setState((current) => ({ ...current, workflow }))
        } catch (workflowError) {
          if (workflowError.name !== 'AbortError') {
            setState((current) => ({ ...current, workflowError }))
          }
        }
      })
      .catch((error) => {
        if (error.name !== 'AbortError') {
          setState({ loading: false, item: null, workflow: null, error, workflowError: null })
        }
      })
    return () => controller.abort()
  }, [auth, reviewId, attempt])

  const actions = useMemo(() => {
    if (!state.item || state.item.status !== 'pending') return []
    return REVIEW_DECISIONS.filter(([decision]) => {
      if (decision === 'approve' && state.item.category === 'invalid_citation') return false
      if (decision === 'request_re_evaluation') {
        return (
          state.item.operation_type === 'bid_workflow' &&
          state.item.source_stage === 'validate_extraction'
        )
      }
      return true
    })
  }, [state.item])

  async function refreshItem() {
    const item = await getReview(reviewId, auth)
    setState((current) => ({ ...current, item }))
    try {
      const workflow = await getWorkflowReviewStatus(item.workflow_id, auth)
      setState((current) => ({ ...current, workflow, workflowError: null }))
    } catch (workflowError) {
      setState((current) => ({ ...current, workflowError }))
    }
  }

  async function submitDecision(values) {
    const payload = {
      decision: confirmation.decision,
      idempotency_key: confirmation.idempotencyKey,
      comments: values.comments,
      corrected_value: values.corrected_value,
    }
    setDecisionState((current) => ({ ...current, pending: true, error: null, refreshError: null }))
    try {
      const outcome = await submitReviewDecision(reviewId, payload, auth)
      setDecisionState({ pending: false, error: null, outcome, refreshError: null })
      setConfirmation(null)
      try {
        await refreshItem()
      } catch (refreshError) {
        setDecisionState({ pending: false, error: null, outcome, refreshError })
      }
    } catch (error) {
      setDecisionState((current) => ({ ...current, pending: false, error }))
      try {
        await refreshItem()
      } catch {
        // Preserve the decision error; the queue can be reloaded manually.
      }
    }
  }

  const item = state.item
  const profileHref = item
    ? `/bids/${item.bid_id}${item.field_name ? `#${profileFieldAnchor(item.field_name)}` : ''}`
    : ''

  return (
    <PageContainer
      title="Review Detail"
      description={`Evidence and decision history for review ${reviewId}`}
      actions={
        <Link className="button button-secondary" to="/reviews">
          <ArrowLeft size={16} /> Review queue
        </Link>
      }
    >
      <ReviewerSession />
      {auth && state.loading && <LoadingState label="Loading protected review detail" />}
      {auth && state.error && (
        <ErrorState
          title={state.error.status === 403 ? 'Reviewer authorization failed' : 'Unable to load review'}
          message={state.error.message}
          onRetry={() => setAttempt((value) => value + 1)}
        />
      )}
      {auth && item && (
        <>
          <section className="panel review-detail-header">
            <div>
              <span className="eyebrow">{item.operation_type.replaceAll('_', ' ')}</span>
              <h2>{item.field_name || item.item_reference}</h2>
              <p>{item.reason}</p>
            </div>
            <div>
              <StatusBadge status={item.severity} />
              <StatusBadge status={item.status} />
            </div>
          </section>

          <section className="review-detail-grid">
            <article className="panel">
              <span className="eyebrow">Model output</span>
              <h2>Original proposal</h2>
              <div className="review-proposal prominent">
                <ValueDisplay value={item.proposed_value} label="Original model proposal" />
              </div>
              {item.corrected_value !== null && item.corrected_value !== undefined && (
                <div className="human-correction">
                  <span>Persisted human-corrected value</span>
                  <ValueDisplay value={item.corrected_value} label="Human-corrected value" />
                </div>
              )}
              <dl className="review-detail-facts">
                <div><dt>Review ID</dt><dd><code>{item.review_item_id}</code></dd></div>
                <div><dt>Bid ID</dt><dd><code>{item.bid_id}</code></dd></div>
                <div><dt>Workflow ID</dt><dd><code>{item.workflow_id}</code></dd></div>
                <div><dt>Item reference</dt><dd>{item.item_reference}</dd></div>
                <div><dt>Category</dt><dd>{item.category.replaceAll('_', ' ')}</dd></div>
                <div><dt>Source stage</dt><dd>{item.source_stage.replaceAll('_', ' ')}</dd></div>
                <div><dt>Validation status</dt><dd>{item.validation_status?.replaceAll('_', ' ') || 'Unavailable'}</dd></div>
                {item.confidence_score != null && (
                  <div><dt>Confidence</dt><dd>{Math.round(item.confidence_score * 100)}%</dd></div>
                )}
                <div><dt>Created</dt><dd>{new Date(item.created_at).toLocaleString()}</dd></div>
                <div><dt>Reviewer</dt><dd>{item.reviewer_id || 'Not assigned'}</dd></div>
              </dl>
              <Link className="button button-secondary" to={profileHref} target="_blank" rel="noreferrer">
                Open related profile evidence <ExternalLink size={14} />
              </Link>
            </article>

            <aside className="panel workflow-review-status">
              <div className="panel-heading">
                <div><span className="eyebrow">Review aggregate</span><h2>Workflow review status</h2></div>
                <Workflow size={21} />
              </div>
              {state.workflow ? (
                <>
                  <dl>
                    <div><dt>Total</dt><dd>{state.workflow.total}</dd></div>
                    <div><dt>Pending</dt><dd>{state.workflow.pending}</dd></div>
                    <div><dt>Approved</dt><dd>{state.workflow.approved}</dd></div>
                    <div><dt>Rejected</dt><dd>{state.workflow.rejected}</dd></div>
                    <div><dt>Unresolved</dt><dd>{state.workflow.unresolved}</dd></div>
                    <div><dt>Re-evaluation</dt><dd>{state.workflow.reevaluation_requested}</dd></div>
                  </dl>
                  <p>
                    {state.workflow.all_required_resolved
                      ? 'All required review items are resolved.'
                      : 'The workflow still has review work pending.'}
                  </p>
                </>
              ) : (
                <p className="muted">Workflow review status is unavailable.</p>
              )}
              {state.workflowError && <p className="config-error">{state.workflowError.message}</p>}
              <small>This aggregate does not prove that LangGraph workflow execution completed.</small>
            </aside>
          </section>

          {decisionState.outcome && (
            <section className="decision-outcome" role="status">
              <ShieldCheck size={20} />
              <div>
                <strong>Decision persisted by the backend</strong>
                <p>
                  {decisionState.outcome.workflow_resumed
                    ? `Workflow resume was confirmed. Reported status: ${decisionState.outcome.workflow_status || 'unavailable'}.`
                    : `Workflow was not resumed. Reported status: ${decisionState.outcome.workflow_status || 'unavailable'}.`}
                </p>
                {decisionState.outcome.detail && <p>{decisionState.outcome.detail}</p>}
                {decisionState.refreshError && (
                  <p className="config-error">
                    The decision was persisted, but refreshed review status is unavailable:{' '}
                    {decisionState.refreshError.message}
                  </p>
                )}
              </div>
            </section>
          )}

          <section className="panel review-evidence">
            <span className="eyebrow">Source traceability</span>
            <h2>Review evidence</h2>
            <div>
              <section>
                <h3>Supporting citations</h3>
                <CitationList citations={item.supporting_citations} role="supporting" />
              </section>
              <section>
                <h3>Contradicting citations</h3>
                <CitationList citations={item.contradicting_citations} role="contradicting" />
              </section>
            </div>
            <p className="citation-viewer-note">
              Citation presence is not displayed as independent validity because the review schema
              does not expose per-citation validation status.
            </p>
          </section>

          <section className="panel review-history">
            <div className="panel-heading">
              <div><span className="eyebrow">Immutable audit context</span><h2>Decision history</h2></div>
              <History size={21} />
            </div>
            {item.decisions?.length ? (
              <ol>
                {item.decisions.map((decision) => (
                  <li key={decision.decision_id}>
                    <div>
                      <StatusBadge status={decision.decision} />
                      <strong>{decision.reviewer_id}</strong>
                      <time>{new Date(decision.created_at).toLocaleString()}</time>
                    </div>
                    <p>{decision.comments || 'No reviewer comments.'}</p>
                    {decision.corrected_value !== null && decision.corrected_value !== undefined && (
                      <div className="history-correction">
                        <span>Human-corrected value</span>
                        <ValueDisplay value={decision.corrected_value} label="Historical correction" />
                      </div>
                    )}
                    {decision.result_reference && (
                      <dl className="structured-value">
                        {Object.entries(decision.result_reference).map(([key, value]) => (
                          <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{String(value)}</dd></div>
                        ))}
                      </dl>
                    )}
                  </li>
                ))}
              </ol>
            ) : (
              <EmptyState title="No prior decisions" description="This review item has no persisted decision history." />
            )}
          </section>

          <section className="panel review-actions">
            <span className="eyebrow">Backend-authorized transition</span>
            <h2>Reviewer actions</h2>
            {item.status === 'pending' ? (
              <div>
                {actions.map(([decision, label]) => (
                  <button
                    type="button"
                    className={`button decision-${decision}`}
                    key={decision}
                    onClick={() => {
                      setDecisionState({
                        pending: false,
                        error: null,
                        outcome: null,
                        refreshError: null,
                      })
                      setConfirmation({
                        decision,
                        label,
                        idempotencyKey: createIdempotencyKey(),
                      })
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            ) : (
              <p className="muted">
                This item is already {item.status.replaceAll('_', ' ')}. The backend permits
                decisions only for pending items.
              </p>
            )}
          </section>
        </>
      )}

      {confirmation && item && (
        <ReviewDecisionDialog
          item={item}
          decision={confirmation.decision}
          label={confirmation.label}
          pending={decisionState.pending}
          error={decisionState.error}
          onCancel={() => {
            if (!decisionState.pending) setConfirmation(null)
          }}
          onSubmit={submitDecision}
        />
      )}
    </PageContainer>
  )
}
