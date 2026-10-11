import { ChevronLeft, ChevronRight, DatabaseZap, FolderSearch, Play, Plus, Search, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import DocumentList from '../components/documents/DocumentList'
import EmptyState from '../components/common/EmptyState'
import ErrorState from '../components/common/ErrorState'
import LoadingState from '../components/common/LoadingState'
import StatusBadge from '../components/common/StatusBadge'
import PageContainer from '../components/layout/PageContainer'
import { listBids } from '../services/bidService'
import { processDirectory, scanDirectory } from '../services/ingestionService'
import { documentStatus } from '../utils/documentStatus'
import { formatTimestamp } from '../utils/format'

const PAGE_SIZE = 20

export function BidListContent({ loading, error, bids, onRetry, filtered = false }) {
  if (loading) return <LoadingState label="Loading bids" />
  if (error) return <ErrorState title="Unable to load bids" message={error.message} onRetry={onRetry} />
  if (!bids?.length) {
    return filtered ? (
      <EmptyState title="No matching bids" description="No bids match the current search." />
    ) : (
      <EmptyState
        title="No bids available"
        description="No bids were returned by the backend. Create one by uploading its documents."
        action={<Link className="button button-primary" to="/bids/new"><Plus size={16} /> New bid</Link>}
      />
    )
  }
  return (
    <ul className="bid-table" aria-label="Bids">
      {bids.map((bid) => (
        <li key={bid.id} className="bid-row">
          <div className="bid-row-main">
            <Link to={`/bids/${bid.id}`}><strong>{bid.title || bid.bid_identifier}</strong></Link>
            <span>
              {bid.bid_identifier}
              {bid.solicitation_number ? ` · ${bid.solicitation_number}` : ''}
              {bid.issuing_organization ? ` · ${bid.issuing_organization}` : ''}
            </span>
          </div>
          <div className="bid-row-meta">
            <span>{bid.indexed_document_count}/{bid.document_count} indexed</span>
            <span>Created {formatTimestamp(bid.created_at)}</span>
          </div>
          <div className="bid-row-status">
            {bid.latest_job ? (
              <StatusBadge status={bid.latest_job.status} />
            ) : (
              <span className="muted-chip">Not processed</span>
            )}
            {bid.latest_workflow_status && (
              <span className="muted-chip">Workflow: {bid.latest_workflow_status.replaceAll('_', ' ')}</span>
            )}
          </div>
        </li>
      ))}
    </ul>
  )
}

function summaryItems(summary) {
  if (!summary) return []
  if ('files_registered' in summary) {
    return [
      ['Discovered', summary.files_discovered],
      ['Registered', summary.files_registered],
      ['Duplicates', summary.duplicates_skipped],
      ['Failed', summary.failed_files],
    ]
  }
  return [
    ['Discovered', summary.files_discovered],
    ['Completed', summary.documents_completed],
    ['Skipped', summary.documents_skipped],
    ['Failed', summary.documents_failed],
  ]
}

export default function BidsPage() {
  const controllerRef = useRef(null)
  const [listQuery, setListQuery] = useState({ search: '', offset: 0, attempt: 0 })
  const [searchInput, setSearchInput] = useState('')
  const [listState, setListState] = useState({ loading: true, data: null, error: null })
  const [relativeDirectory, setRelativeDirectory] = useState('')
  const [bidIdentifier, setBidIdentifier] = useState('')
  const [operation, setOperation] = useState({ pending: null, data: null, error: null })
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('all')

  useEffect(() => () => controllerRef.current?.abort(), [])

  useEffect(() => {
    const controller = new AbortController()
    listBids({ limit: PAGE_SIZE, offset: listQuery.offset, search: listQuery.search }, controller.signal)
      .then((data) => setListState({ loading: false, data, error: null }))
      .catch((error) => {
        if (error.name !== 'AbortError') setListState({ loading: false, data: null, error })
      })
    return () => controller.abort()
  }, [listQuery])

  function reloadList(patch) {
    setListState((current) => ({ ...current, loading: true, error: null }))
    setListQuery((current) => ({ ...current, ...patch, attempt: current.attempt + 1 }))
  }

  const results = useMemo(() => operation.data?.results || [], [operation.data])
  const statuses = useMemo(
    () => [...new Set(results.map(documentStatus))].sort(),
    [results],
  )
  const filteredResults = useMemo(() => {
    const term = search.trim().toLowerCase()
    return results.filter((document) => {
      const matchesSearch =
        !term ||
        document.relative_path?.toLowerCase().includes(term) ||
        document.bid_identifier?.toLowerCase().includes(term) ||
        document.bid_id?.toLowerCase().includes(term)
      return matchesSearch && (status === 'all' || documentStatus(document) === status)
    })
  }, [results, search, status])

  async function runIngestion(action) {
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    setOperation({ pending: action, data: null, error: null })
    const values = { relativeDirectory, bidIdentifier }
    try {
      const data =
        action === 'scan'
          ? await scanDirectory(values, controller.signal)
          : await processDirectory(values, controller.signal)
      setOperation({ pending: null, data, error: null })
      setSearch('')
      setStatus('all')
    } catch (error) {
      if (error.name !== 'AbortError') setOperation({ pending: null, data: null, error })
    }
  }

  const total = listState.data?.total ?? 0
  const offset = listQuery.offset

  return (
    <PageContainer
      title="Bids"
      description="Browse bids stored by the backend, upload new bid documents, and track processing."
      actions={
        <Link to="/bids/new" className="button button-primary">
          <Plus size={17} /> New bid
        </Link>
      }
    >
      <section className="panel bid-directory">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">Persistent bid directory</span>
            <h2>All bids{listState.data ? ` (${total})` : ''}</h2>
          </div>
          <DatabaseZap size={22} />
        </div>
        <form
          className="bid-search"
          role="search"
          onSubmit={(event) => {
            event.preventDefault()
            reloadList({ search: searchInput, offset: 0 })
          }}
        >
          <label htmlFor="bid-search" className="visually-hidden">Search bids</label>
          <div className="filter-input">
            <Search size={16} />
            <input
              id="bid-search"
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              placeholder="Identifier, title, solicitation, organization"
            />
          </div>
          <button className="button button-secondary" type="submit">Search</button>
          {listQuery.search && (
            <button
              className="button button-secondary"
              type="button"
              onClick={() => {
                setSearchInput('')
                reloadList({ search: '', offset: 0 })
              }}
            >
              <X size={15} /> Clear
            </button>
          )}
        </form>
        <BidListContent
          loading={listState.loading}
          error={listState.error}
          bids={listState.data?.items}
          filtered={Boolean(listQuery.search)}
          onRetry={() => reloadList({})}
        />
        {total > PAGE_SIZE && (
          <div className="pagination">
            <button
              className="button button-secondary"
              type="button"
              disabled={offset === 0 || listState.loading}
              onClick={() => reloadList({ offset: Math.max(0, offset - PAGE_SIZE) })}
            >
              <ChevronLeft size={16} /> Previous
            </button>
            <span>{offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}</span>
            <button
              className="button button-secondary"
              type="button"
              disabled={offset + PAGE_SIZE >= total || listState.loading}
              onClick={() => reloadList({ offset: offset + PAGE_SIZE })}
            >
              Next <ChevronRight size={16} />
            </button>
          </div>
        )}
      </section>

      <details className="panel advanced-ingestion">
        <summary>
          <FolderSearch size={18} />
          <span>Advanced: server folder ingestion</span>
        </summary>
        <p className="muted">
          For administrators with files already on the server. The backend scans its local{' '}
          <code>INGESTION_INPUT_ROOT</code>; use <Link to="/bids/new">New bid</Link> to upload files
          from this browser instead.
        </p>
        <div className="format-list" aria-label="Supported file types">
          <span>PDF</span><span>HTML</span><span>HTM</span>
        </div>
        <div className="stack-form">
          <label htmlFor="relative-directory">Relative directory</label>
          <input
            id="relative-directory"
            value={relativeDirectory}
            onChange={(event) => setRelativeDirectory(event.target.value)}
            placeholder="Bid1"
          />
          <small>Leave blank to scan the complete configured ingestion root.</small>
          <label htmlFor="bid-identifier">Bid identifier override</label>
          <input
            id="bid-identifier"
            value={bidIdentifier}
            maxLength="128"
            onChange={(event) => setBidIdentifier(event.target.value)}
            placeholder="Optional internal grouping identifier"
          />
          <div className="ingestion-actions">
            <button
              className="button button-secondary"
              type="button"
              disabled={Boolean(operation.pending)}
              onClick={() => runIngestion('scan')}
            >
              <Search size={17} />
              {operation.pending === 'scan' ? 'Scanning…' : 'Scan metadata'}
            </button>
            <button
              className="button button-primary"
              type="button"
              disabled={Boolean(operation.pending)}
              onClick={() => runIngestion('process')}
            >
              <Play size={17} />
              {operation.pending === 'process' ? 'Processing…' : 'Process & index'}
            </button>
          </div>
        </div>

      <section className="ingestion-results" aria-live="polite">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">Operation results</span>
            <h2>Documents returned by the backend</h2>
          </div>
        </div>
        {operation.pending && <LoadingState label={operation.pending === 'scan' ? 'Scanning source directory' : 'Processing and indexing documents'} />}
        {operation.error && (
          <ErrorState
            title="Ingestion request failed"
            message={operation.error.message}
            onRetry={() => runIngestion('scan')}
          />
        )}
        {!operation.pending && !operation.error && !operation.data && (
          <EmptyState
            title="No ingestion operation submitted"
            description="Choose scan or process. Results appear only after the backend confirms the operation."
          />
        )}
        {operation.data && (
          <>
            <div className="summary-strip">
              {summaryItems(operation.data).map(([label, value]) => (
                <div key={label}><span>{label}</span><strong>{value ?? 0}</strong></div>
              ))}
            </div>
            {results.length > 0 && (
              <div className="document-filters">
                <label>
                  <span>Search results</span>
                  <input
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                    placeholder="Filename, path, or bid ID"
                  />
                </label>
                <label>
                  <span>Status</span>
                  <select value={status} onChange={(event) => setStatus(event.target.value)}>
                    <option value="all">All statuses</option>
                    {statuses.map((item) => <option key={item} value={item}>{item.replaceAll('_', ' ')}</option>)}
                  </select>
                </label>
                {(search || status !== 'all') && (
                  <button className="button button-secondary clear-filters" onClick={() => { setSearch(''); setStatus('all') }}>
                    <X size={15} /> Clear
                  </button>
                )}
              </div>
            )}
            {filteredResults.length ? (
              <DocumentList documents={filteredResults} />
            ) : (
              <EmptyState
                title={results.length ? 'No matching documents' : 'No documents discovered'}
                description={results.length ? 'Clear or adjust the current filters.' : 'The backend returned an empty result set.'}
              />
            )}
          </>
        )}
      </section>
      </details>
    </PageContainer>
  )
}
