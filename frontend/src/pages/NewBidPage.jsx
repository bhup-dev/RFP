import { ArrowLeft, FilePlus2, Play, Trash2, UploadCloud } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import JobStatus from '../components/bids/JobStatus'
import ErrorState from '../components/common/ErrorState'
import LoadingState from '../components/common/LoadingState'
import StatusBadge from '../components/common/StatusBadge'
import PageContainer from '../components/layout/PageContainer'
import { FALLBACK_UPLOAD_LIMITS, ROLE_LABELS } from '../constants/uploads'
import {
  createBid,
  getBid,
  getJob,
  getUploadLimits,
  startProcessing,
  uploadDocuments,
} from '../services/bidService'
import { fileExtension, formatBytes } from '../utils/format'

let entryCounter = 0

function clientProblem(file, limits) {
  if (!limits.allowed_extensions.includes(fileExtension(file.name))) {
    return `Unsupported type. Allowed: ${limits.allowed_extensions.join(', ')}`
  }
  if (file.size === 0) return 'File is empty'
  if (file.size > limits.max_file_bytes) {
    return `Larger than the ${formatBytes(limits.max_file_bytes)} limit`
  }
  return null
}

export default function NewBidPage() {
  const [searchParams] = useSearchParams()
  const existingBidId = searchParams.get('bid')
  const inputRef = useRef(null)
  const controllerRef = useRef(null)

  const [limits, setLimits] = useState(FALLBACK_UPLOAD_LIMITS)
  const [bid, setBid] = useState(null)
  const [bidLoad, setBidLoad] = useState({ loading: Boolean(existingBidId), error: null })
  const [form, setForm] = useState({
    bidIdentifier: '',
    title: '',
    solicitationNumber: '',
    issuingOrganization: '',
  })
  const [entries, setEntries] = useState([])
  const [dragging, setDragging] = useState(false)
  const [phase, setPhase] = useState('select')
  const [formError, setFormError] = useState(null)
  const [job, setJob] = useState(null)
  const [processing, setProcessing] = useState({ pending: false, error: null })

  useEffect(() => {
    const controller = new AbortController()
    getUploadLimits(controller.signal)
      .then(setLimits)
      .catch(() => {})
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (!existingBidId) return undefined
    const controller = new AbortController()
    getBid(existingBidId, controller.signal)
      .then((data) => {
        setBid(data)
        setBidLoad({ loading: false, error: null })
      })
      .catch((error) => {
        if (error.name !== 'AbortError') setBidLoad({ loading: false, error })
      })
    return () => controller.abort()
  }, [existingBidId])

  useEffect(() => () => controllerRef.current?.abort(), [])

  function addFiles(fileList) {
    const files = Array.from(fileList || [])
    if (!files.length) return
    setEntries((current) => [
      ...current,
      ...files.map((file) => {
        entryCounter += 1
        return {
          key: `${entryCounter}-${file.name}`,
          file,
          role: 'auto',
          addendumNumber: '',
          problem: clientProblem(file, limits),
          progress: 0,
          result: null,
        }
      }),
    ])
  }

  function updateEntry(key, patch) {
    setEntries((current) => current.map((entry) => (entry.key === key ? { ...entry, ...patch } : entry)))
  }

  function removeEntry(key) {
    setEntries((current) => current.filter((entry) => entry.key !== key))
  }

  async function ensureBid(signal) {
    if (bid) return bid
    const created = await createBid(form, signal)
    setBid(created)
    return created
  }

  async function submit(event) {
    event.preventDefault()
    setFormError(null)
    const uploadable = entries.filter((entry) => !entry.problem && !entry.result)
    if (!uploadable.length) {
      setFormError(new Error('Add at least one supported file to upload.'))
      return
    }
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    setPhase('uploading')

    let target
    try {
      target = await ensureBid(controller.signal)
    } catch (error) {
      if (error.name !== 'AbortError') {
        setFormError(error)
        setPhase('select')
      }
      return
    }

    for (const entry of uploadable) {
      updateEntry(entry.key, { progress: 0 })
      try {
        const response = await uploadDocuments(
          target.id,
          [{ file: entry.file, role: entry.role, addendumNumber: entry.addendumNumber }],
          {
            signal: controller.signal,
            onProgress: (fraction) => updateEntry(entry.key, { progress: fraction }),
          },
        )
        updateEntry(entry.key, { progress: 1, result: response.results[0] })
      } catch (error) {
        if (error.name === 'AbortError') return
        updateEntry(entry.key, {
          result: { outcome: 'rejected', reason: error.message, original_filename: entry.file.name },
        })
      }
    }
    setPhase('uploaded')
  }

  async function beginProcessing() {
    if (!bid) return
    setProcessing({ pending: true, error: null })
    try {
      const queued = await startProcessing(bid.id)
      setJob(queued)
      setProcessing({ pending: false, error: null })
    } catch (error) {
      const activeJobId = error.status === 409 ? error.details?.job_id : null
      if (activeJobId) {
        try {
          setJob(await getJob(activeJobId))
          setProcessing({ pending: false, error: null })
          return
        } catch (lookupError) {
          setProcessing({ pending: false, error: lookupError })
          return
        }
      }
      setProcessing({ pending: false, error })
    }
  }

  const registered = entries.filter((entry) => entry.result?.outcome === 'registered').length
  const duplicates = entries.filter((entry) => entry.result?.outcome === 'duplicate').length
  const hasDocuments = registered + duplicates > 0 || (bid?.document_count ?? 0) > 0
  const locked = phase === 'uploading' || Boolean(job)
  const existingConflict = formError?.status === 409 ? formError.details?.bid_id : null

  return (
    <PageContainer
      title={existingBidId ? 'Upload bid documents' : 'New bid'}
      description="Upload the original solicitation, addenda, and attachments. Files are validated on the server, stored with the bid, then parsed, indexed, and analyzed in the background."
      actions={
        <Link to={bid ? `/bids/${bid.id}` : '/bids'} className="button button-secondary">
          <ArrowLeft size={17} /> {bid ? 'Back to bid' : 'Back to bids'}
        </Link>
      }
    >
      {bidLoad.loading && <LoadingState label="Loading bid" />}
      {bidLoad.error && (
        <ErrorState title="Unable to load the bid" message={bidLoad.error.message} />
      )}

      {!bidLoad.loading && !bidLoad.error && (
        <form className="upload-layout" onSubmit={submit} noValidate>
          <section className="panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">Step 1</span>
                <h2>{bid ? 'Bid' : 'Describe the bid'}</h2>
              </div>
              <FilePlus2 size={22} />
            </div>
            {bid ? (
              <div className="bid-identity">
                <strong>{bid.title || bid.bid_identifier}</strong>
                <span>{bid.bid_identifier}</span>
                <span>{bid.document_count ?? 0} document(s) already stored</span>
              </div>
            ) : (
              <div className="stack-form">
                <label htmlFor="bid-identifier">Bid identifier *</label>
                <input
                  id="bid-identifier"
                  value={form.bidIdentifier}
                  maxLength="128"
                  required
                  disabled={locked}
                  onChange={(event) => setForm({ ...form, bidIdentifier: event.target.value })}
                  placeholder="e.g. Springfield-Docks-2026"
                />
                <small>Internal name, unique per bid. Letters, digits, spaces, and . _ - ( ).</small>
                <label htmlFor="bid-title">Title</label>
                <input
                  id="bid-title"
                  value={form.title}
                  disabled={locked}
                  onChange={(event) => setForm({ ...form, title: event.target.value })}
                />
                <label htmlFor="bid-solicitation">Solicitation number</label>
                <input
                  id="bid-solicitation"
                  value={form.solicitationNumber}
                  disabled={locked}
                  onChange={(event) => setForm({ ...form, solicitationNumber: event.target.value })}
                />
                <label htmlFor="bid-organization">Issuing organization</label>
                <input
                  id="bid-organization"
                  value={form.issuingOrganization}
                  disabled={locked}
                  onChange={(event) => setForm({ ...form, issuingOrganization: event.target.value })}
                />
              </div>
            )}
          </section>

          <section className="panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">Step 2</span>
                <h2>Add documents</h2>
              </div>
              <UploadCloud size={22} />
            </div>
            <div
              className={`drop-zone ${dragging ? 'is-dragging' : ''} ${locked ? 'is-disabled' : ''}`}
              onDragOver={(event) => {
                event.preventDefault()
                if (!locked) setDragging(true)
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault()
                setDragging(false)
                if (!locked) addFiles(event.dataTransfer.files)
              }}
            >
              <UploadCloud size={30} />
              <p>
                Drag files here or{' '}
                <button type="button" className="link-button" disabled={locked} onClick={() => inputRef.current?.click()}>
                  choose files
                </button>
              </p>
              <small>
                {limits.allowed_extensions.join(', ')} · up to {formatBytes(limits.max_file_bytes)} each
              </small>
              <input
                ref={inputRef}
                className="visually-hidden"
                type="file"
                multiple
                aria-label="Choose files"
                accept={limits.allowed_extensions.join(',')}
                disabled={locked}
                onChange={(event) => {
                  addFiles(event.target.files)
                  event.target.value = ''
                }}
              />
            </div>

            {entries.length > 0 && (
              <ul className="upload-list" aria-label="Selected files">
                {entries.map((entry) => (
                  <li key={entry.key} className="upload-item">
                    <div className="upload-item-main">
                      <strong title={entry.file.name}>{entry.file.name}</strong>
                      <span>{formatBytes(entry.file.size)}</span>
                    </div>
                    {!entry.result && !entry.problem && (
                      <div className="upload-item-controls">
                        <label>
                          <span className="visually-hidden">Role for {entry.file.name}</span>
                          <select
                            value={entry.role}
                            disabled={locked}
                            onChange={(event) => updateEntry(entry.key, { role: event.target.value })}
                          >
                            {limits.roles.map((role) => (
                              <option key={role} value={role}>{ROLE_LABELS[role] || role}</option>
                            ))}
                          </select>
                        </label>
                        {entry.role === 'addendum' && (
                          <label>
                            <span className="visually-hidden">Addendum number for {entry.file.name}</span>
                            <input
                              type="number"
                              min="1"
                              max="999"
                              placeholder="No."
                              value={entry.addendumNumber}
                              disabled={locked}
                              onChange={(event) => updateEntry(entry.key, { addendumNumber: event.target.value })}
                            />
                          </label>
                        )}
                      </div>
                    )}
                    {entry.problem && <span className="upload-problem">{entry.problem}</span>}
                    {phase === 'uploading' && !entry.result && !entry.problem && (
                      <progress
                        value={Math.round(entry.progress * 100)}
                        max="100"
                        aria-label={`Upload progress for ${entry.file.name}`}
                      />
                    )}
                    {entry.result && (
                      <div className="upload-result">
                        <StatusBadge status={entry.result.outcome} />
                        {entry.result.reason && <span>{entry.result.reason}</span>}
                      </div>
                    )}
                    {!locked && !entry.result && (
                      <button
                        type="button"
                        className="icon-button"
                        aria-label={`Remove ${entry.file.name}`}
                        onClick={() => removeEntry(entry.key)}
                      >
                        <Trash2 size={16} />
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            )}

            {formError && (
              <div className="form-error" role="alert">
                {formError.message}
                {existingConflict && (
                  <> <Link to={`/bids/${existingConflict}`}>Open the existing bid</Link> or <Link to={`/bids/new?bid=${existingConflict}`}>add files to it</Link>.</>
                )}
              </div>
            )}

            {!job && (
              <div className="ingestion-actions">
                <button
                  className="button button-primary"
                  type="submit"
                  disabled={locked || (!bid && !form.bidIdentifier.trim())}
                >
                  <UploadCloud size={17} />
                  {phase === 'uploading' ? 'Uploading…' : bid ? 'Upload files' : 'Create bid & upload'}
                </button>
              </div>
            )}
          </section>

          {(phase === 'uploaded' || job || (bid && hasDocuments)) && (
            <section className="panel upload-process-panel">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">Step 3</span>
                  <h2>Process &amp; analyze</h2>
                </div>
                <Play size={22} />
              </div>
              {phase === 'uploaded' && (
                <p className="muted">
                  {registered} file(s) stored, {duplicates} already present
                  {entries.some((entry) => entry.result?.outcome === 'rejected') && ', some rejected (see reasons above)'}.
                </p>
              )}
              {processing.error && (
                <ErrorState title="Unable to start processing" message={processing.error.message} />
              )}
              {job ? (
                <JobStatus key={job.id} job={job} onRetry={beginProcessing} retrying={processing.pending} />
              ) : (
                <button
                  className="button button-primary"
                  type="button"
                  disabled={!hasDocuments || processing.pending || phase === 'uploading'}
                  onClick={beginProcessing}
                >
                  <Play size={17} /> {processing.pending ? 'Queuing…' : 'Start processing'}
                </button>
              )}
            </section>
          )}
        </form>
      )}
    </PageContainer>
  )
}
