import { AlertTriangle, CheckCircle2, Circle, Loader2, RotateCcw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import StatusBadge from '../common/StatusBadge'
import { getJob, JOB_ACTIVE_STATUSES } from '../../services/bidService'

const POLL_START_MS = 2000
const POLL_MAX_MS = 10000

function stepsFor(jobType) {
  const steps = [['queued', 'Queued']]
  if (jobType !== 'analyze_only') steps.push(['ingesting', 'Parsing & indexing'])
  steps.push(['analyzing', 'Analyzing'], ['done', 'Done'])
  return steps
}

function stepState(job, stepKey, steps) {
  const order = steps.map(([key]) => key)
  const current = job.status === 'succeeded' ? 'done' : job.stage
  const currentIndex = order.indexOf(current)
  const index = order.indexOf(stepKey)
  if (job.status === 'succeeded') return 'complete'
  if (index < currentIndex) return 'complete'
  if (index === currentIndex) return job.status === 'failed' ? 'failed' : 'active'
  return 'upcoming'
}

function formatTime(value) {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function StepIcon({ state }) {
  if (state === 'complete') return <CheckCircle2 size={18} />
  if (state === 'active') return <Loader2 size={18} className="spin" />
  if (state === 'failed') return <AlertTriangle size={18} />
  return <Circle size={18} />
}

export function JobSummary({ job }) {
  const ingestion = job.result_summary?.ingestion
  const workflow = job.result_summary?.workflow
  if (!ingestion && !workflow) return null
  return (
    <div className="job-summary">
      {ingestion && (
        <div className="summary-strip">
          <div><span>Documents</span><strong>{ingestion.documents_total ?? 0}</strong></div>
          <div><span>Indexed</span><strong>{ingestion.documents_completed ?? 0}</strong></div>
          <div><span>Failed</span><strong>{ingestion.documents_failed ?? 0}</strong></div>
          <div><span>Chunks</span><strong>{ingestion.chunks_created ?? 0}</strong></div>
        </div>
      )}
      {ingestion?.failures?.length > 0 && (
        <ul className="job-failures">
          {ingestion.failures.map((failure) => (
            <li key={failure.document_id || failure.relative_path}>
              <strong>{failure.relative_path?.split('/').pop()}</strong>: {failure.errors.join('; ') || 'failed'}
            </li>
          ))}
        </ul>
      )}
      {workflow && (
        <p className="muted job-workflow-line">
          Workflow <StatusBadge status={workflow.status} />
          {workflow.requires_human_review && <span className="review-indicator"><AlertTriangle size={14} /> Human review required</span>}
        </p>
      )}
    </div>
  )
}

export default function JobStatus({
  job: initialJob,
  onFinished,
  onRetry,
  retrying = false,
  showProfileLink = true,
}) {
  const [job, setJob] = useState(initialJob)
  const [pollError, setPollError] = useState(null)
  const finishedRef = useRef(false)
  const onFinishedRef = useRef(onFinished)

  useEffect(() => {
    onFinishedRef.current = onFinished
  }, [onFinished])

  const jobId = job.id
  const active = JOB_ACTIVE_STATUSES.has(job.status)

  useEffect(() => {
    if (!active) return undefined
    const controller = new AbortController()
    let delay = POLL_START_MS
    let lastStage = null
    let timer

    const poll = async () => {
      try {
        const next = await getJob(jobId, controller.signal)
        setPollError(null)
        setJob(next)
        if (!JOB_ACTIVE_STATUSES.has(next.status)) return
        delay = next.stage === lastStage ? Math.min(delay * 1.5, POLL_MAX_MS) : POLL_START_MS
        lastStage = next.stage
      } catch (error) {
        if (error.name === 'AbortError') return
        setPollError(error)
        delay = POLL_MAX_MS
      }
      timer = setTimeout(poll, delay)
    }
    timer = setTimeout(poll, delay)
    return () => {
      controller.abort()
      clearTimeout(timer)
    }
  }, [jobId, active])

  useEffect(() => {
    if (!active && !finishedRef.current && job.id === initialJob.id && job !== initialJob) {
      finishedRef.current = true
      onFinishedRef.current?.(job)
    }
  }, [active, job, initialJob])

  const steps = stepsFor(job.job_type)
  const retryScheduled = job.status === 'queued' && job.attempts > 0 && job.error_code

  return (
    <section className="job-status" aria-live="polite" aria-label="Processing status">
      <div className="job-status-heading">
        <div>
          <span className="eyebrow">Background processing</span>
          <strong>{job.job_type === 'analyze_only' ? 'Analysis' : 'Ingestion & analysis'}</strong>
        </div>
        <StatusBadge status={job.status} />
      </div>
      <ol className="job-steps">
        {steps.map(([key, label]) => {
          const state = stepState(job, key, steps)
          return (
            <li key={key} className={`job-step job-step-${state}`}>
              <StepIcon state={state} /> <span>{label}</span>
            </li>
          )
        })}
      </ol>
      <p className="muted job-meta">
        Attempt {Math.max(job.attempts, 1)} of {job.max_attempts}
        {job.started_at && ` · started ${formatTime(job.started_at)}`}
        {job.finished_at && ` · finished ${formatTime(job.finished_at)}`}
      </p>
      {retryScheduled && (
        <p className="job-retry-note">
          The last attempt failed ({job.error_detail || job.error_code}). A retry is scheduled
          {job.available_at ? ` for ${formatTime(job.available_at)}` : ''}.
        </p>
      )}
      {pollError && (
        <p className="review-integration-warning">
          Status updates are delayed: {pollError.message}
        </p>
      )}
      {job.status === 'failed' && (
        <div className="state-card state-error" role="alert">
          <AlertTriangle size={22} />
          <div>
            <strong>Processing failed{job.error_code ? ` (${job.error_code.replaceAll('_', ' ')})` : ''}</strong>
            <p>{job.error_detail || 'The backend did not report an error detail.'}</p>
            {onRetry && (
              <button className="button button-secondary" type="button" onClick={onRetry} disabled={retrying}>
                <RotateCcw size={16} /> {retrying ? 'Queuing…' : 'Retry processing'}
              </button>
            )}
          </div>
        </div>
      )}
      <JobSummary job={job} />
      {job.status === 'succeeded' && (
        <div className="job-success">
          <CheckCircle2 size={20} />
          <span>Processing finished. The intelligence profile reflects the persisted results.</span>
          {showProfileLink && (
            <Link className="button button-primary" to={`/bids/${job.bid_id}`}>
              Open profile
            </Link>
          )}
        </div>
      )}
    </section>
  )
}
