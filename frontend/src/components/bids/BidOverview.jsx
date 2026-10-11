import { Download, ExternalLink, FileText, History, Play, UploadCloud } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import EmptyState from '../common/EmptyState'
import ErrorState from '../common/ErrorState'
import StatusBadge from '../common/StatusBadge'
import JobStatus from './JobStatus'
import {
  documentFileUrl,
  getJob,
  JOB_ACTIVE_STATUSES,
  startProcessing,
} from '../../services/bidService'
import { formatTimestamp } from '../../utils/format'

export default function BidOverview({ bidId, bid, error, onChanged }) {
  const [processing, setProcessing] = useState({ pending: false, error: null, job: null })

  if (error) {
    return (
      <section className="panel">
        <ErrorState title="Documents and processing unavailable" message={error.message} />
      </section>
    )
  }
  if (!bid) return null

  const latestJob = processing.job || bid.jobs?.[0] || null
  const activeJob = latestJob && JOB_ACTIVE_STATUSES.has(latestJob.status) ? latestJob : null
  const documents = bid.documents || []

  async function runProcessing() {
    setProcessing({ pending: true, error: null, job: null })
    try {
      const job = await startProcessing(bidId)
      setProcessing({ pending: false, error: null, job })
    } catch (requestError) {
      const activeId = requestError.status === 409 ? requestError.details?.job_id : null
      if (activeId) {
        try {
          setProcessing({ pending: false, error: null, job: await getJob(activeId) })
          return
        } catch (lookupError) {
          setProcessing({ pending: false, error: lookupError, job: null })
          return
        }
      }
      setProcessing({ pending: false, error: requestError, job: null })
    }
  }

  return (
    <section className="panel bid-overview" aria-label="Documents and processing">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">Registered sources</span>
          <h2>Documents &amp; processing</h2>
        </div>
        <div className="bid-overview-actions">
          <Link
            className={`button button-secondary ${activeJob ? 'is-disabled' : ''}`}
            to={`/bids/new?bid=${bidId}`}
            aria-disabled={Boolean(activeJob)}
            onClick={(event) => activeJob && event.preventDefault()}
          >
            <UploadCloud size={16} /> Upload documents
          </Link>
          <button
            className="button button-primary"
            type="button"
            onClick={runProcessing}
            disabled={!documents.length || Boolean(activeJob) || processing.pending}
          >
            <Play size={16} /> {processing.pending ? 'Queuing…' : 'Process & analyze'}
          </button>
        </div>
      </div>

      {processing.error && (
        <ErrorState title="Unable to start processing" message={processing.error.message} />
      )}
      {latestJob && (activeJob || processing.job) && (
        <JobStatus
          key={latestJob.id}
          job={latestJob}
          showProfileLink={false}
          onRetry={runProcessing}
          retrying={processing.pending}
          onFinished={() => {
            setProcessing({ pending: false, error: null, job: null })
            onChanged?.()
          }}
        />
      )}

      {documents.length ? (
        <ul className="source-documents" aria-label="Source documents">
          {documents.map((document) => {
            const isPdf = document.document_type === 'pdf'
            return (
              <li key={document.id} className="source-document">
                <FileText size={18} />
                <div className="source-document-main">
                  <strong title={document.original_filename || document.file_name}>
                    {document.original_filename || document.file_name}
                  </strong>
                  <span>
                    {(document.document_type || 'unknown').toUpperCase()}
                    {document.document_role ? ` · ${document.document_role}` : ' · role detected from content'}
                    {document.addendum_number ? ` #${document.addendum_number}` : ''}
                    {` · added ${formatTimestamp(document.uploaded_at || document.created_at)}`}
                  </span>
                </div>
                <StatusBadge status={document.ingestion_status} />
                <div className="source-document-links">
                  {isPdf && (
                    <a href={documentFileUrl(document.id)} target="_blank" rel="noreferrer" className="icon-button" aria-label={`Open ${document.original_filename || document.file_name}`}>
                      <ExternalLink size={16} />
                    </a>
                  )}
                  <a href={documentFileUrl(document.id, { download: true })} className="icon-button" aria-label={`Download ${document.original_filename || document.file_name}`}>
                    <Download size={16} />
                  </a>
                </div>
              </li>
            )
          })}
        </ul>
      ) : (
        <EmptyState
          title="No documents yet"
          description="Upload the solicitation and any addenda to start processing this bid."
        />
      )}

      {bid.jobs?.length > 0 && (
        <details className="job-history">
          <summary><History size={16} /> Processing history ({bid.jobs.length})</summary>
          <ul>
            {bid.jobs.map((job) => (
              <li key={job.id}>
                <StatusBadge status={job.status} />
                <span>{job.job_type === 'analyze_only' ? 'Analysis' : 'Ingestion & analysis'}</span>
                <span>{formatTimestamp(job.created_at)}</span>
                {job.error_detail && <span className="job-history-error">{job.error_detail}</span>}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}
