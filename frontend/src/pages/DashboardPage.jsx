import { Activity, ArrowRight, Database, FilePlus2, GitCompareArrows, HeartPulse } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import ErrorState from '../components/common/ErrorState'
import LoadingState from '../components/common/LoadingState'
import StatusBadge from '../components/common/StatusBadge'
import PageContainer from '../components/layout/PageContainer'
import { listRecentJobs } from '../services/bidService'
import { getReadiness } from '../services/healthService'
import { formatTimestamp } from '../utils/format'

export default function DashboardPage() {
  const [state, setState] = useState({ loading: true, data: null, error: null })
  const [attempt, setAttempt] = useState(0)
  const [jobs, setJobs] = useState({ loading: true, data: null, error: null })

  useEffect(() => {
    const controller = new AbortController()
    getReadiness(controller.signal)
      .then((data) => setState({ loading: false, data, error: null }))
      .catch((error) => {
        if (error.name !== 'AbortError') setState({ loading: false, data: null, error })
      })
    return () => controller.abort()
  }, [attempt])

  useEffect(() => {
    const controller = new AbortController()
    listRecentJobs(8, controller.signal)
      .then((data) => setJobs({ loading: false, data, error: null }))
      .catch((error) => {
        if (error.name !== 'AbortError') setJobs({ loading: false, data: null, error })
      })
    return () => controller.abort()
  }, [])

  return (
    <PageContainer
      title="Dashboard"
      description="Monitor backend readiness and move into evidence-grounded bid workflows."
    >
      <div className="dashboard-grid">
        <section className="panel readiness-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">Platform readiness</span>
              <h2>Connected services</h2>
            </div>
            <HeartPulse size={23} />
          </div>
          {state.loading && <LoadingState label="Checking backend readiness" />}
          {state.error && (
            <ErrorState
              title="Backend readiness unavailable"
              message={state.error.message}
              onRetry={() => {
                setState({ loading: true, data: null, error: null })
                setAttempt((value) => value + 1)
              }}
            />
          )}
          {state.data && (
            <div className="service-list">
              {['status', 'postgresql', 'qdrant'].map((key) => (
                <div className="service-row" key={key}>
                  <div>
                    {key === 'status' ? <HeartPulse size={18} /> : <Database size={18} />}
                    <span>{key === 'status' ? 'API' : key}</span>
                  </div>
                  <StatusBadge status={state.data[key] || 'unknown'} />
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="panel action-panel">
          <span className="eyebrow">Start here</span>
          <h2>Upload and analyze a bid</h2>
          <p>
            Create a bid, upload its solicitation and addenda, and track background processing
            until the evidence-grounded profile is ready. Then compare bids side by side.
          </p>
          <div className="action-links">
            <Link className="button button-primary" to="/bids/new">
              <FilePlus2 size={17} /> New bid
            </Link>
            <Link className="button button-secondary" to="/bids">
              Browse bids <ArrowRight size={17} />
            </Link>
            <Link className="button button-secondary" to="/comparison">
              <GitCompareArrows size={17} /> Compare bids
            </Link>
          </div>
        </section>
      </div>

      <section className="panel recent-panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">Recent activity</span>
            <h2>Processing jobs</h2>
          </div>
          <Activity size={22} />
        </div>
        {jobs.loading && <LoadingState label="Loading recent jobs" />}
        {jobs.error && <ErrorState title="Recent jobs unavailable" message={jobs.error.message} />}
        {jobs.data && jobs.data.length === 0 && (
          <p className="muted">No processing jobs have been started yet.</p>
        )}
        {jobs.data?.length > 0 && (
          <ul className="recent-jobs">
            {jobs.data.map((job) => (
              <li key={job.id}>
                <StatusBadge status={job.status} />
                <Link to={`/bids/${job.bid_id}`}>
                  {job.job_type === 'analyze_only' ? 'Analysis' : 'Ingestion & analysis'}
                </Link>
                <span className="muted">{job.stage.replaceAll('_', ' ')}</span>
                <span className="muted">{formatTimestamp(job.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </PageContainer>
  )
}
