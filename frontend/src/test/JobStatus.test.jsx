import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import JobStatus from '../components/bids/JobStatus'
import { getJob } from '../services/bidService'

vi.mock('../services/bidService', async (importOriginal) => ({
  ...(await importOriginal()),
  getJob: vi.fn(),
}))

function job(overrides = {}) {
  return {
    id: 'job-1',
    bid_id: 'bid-1',
    job_type: 'ingest_and_analyze',
    status: 'queued',
    stage: 'queued',
    attempts: 0,
    max_attempts: 3,
    error_code: null,
    error_detail: null,
    result_summary: null,
    ...overrides,
  }
}

function renderStatus(props) {
  return render(
    <MemoryRouter>
      <JobStatus {...props} />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('job status', () => {
  it('polls until the job succeeds, then links to the profile once', async () => {
    vi.useFakeTimers()
    const onFinished = vi.fn()
    getJob
      .mockResolvedValueOnce(job({ status: 'running', stage: 'ingesting', attempts: 1 }))
      .mockResolvedValueOnce(
        job({
          status: 'succeeded',
          stage: 'done',
          attempts: 1,
          result_summary: {
            ingestion: { documents_total: 2, documents_completed: 2, documents_failed: 0, chunks_created: 40 },
            workflow: { status: 'completed', requires_human_review: false },
          },
        }),
      )
    renderStatus({ job: job(), onFinished })

    expect(screen.getByText('Parsing & indexing')).toBeInTheDocument()
    await act(() => vi.advanceTimersByTimeAsync(2000))
    expect(getJob).toHaveBeenCalledTimes(1)
    expect(screen.getByText('running')).toBeInTheDocument()

    await act(() => vi.advanceTimersByTimeAsync(2000))
    expect(getJob).toHaveBeenCalledTimes(2)
    expect(screen.getByRole('link', { name: 'Open profile' })).toHaveAttribute('href', '/bids/bid-1')
    expect(screen.getByText('40')).toBeInTheDocument()
    expect(onFinished).toHaveBeenCalledTimes(1)

    await act(() => vi.advanceTimersByTimeAsync(20000))
    expect(getJob).toHaveBeenCalledTimes(2)
    expect(onFinished).toHaveBeenCalledTimes(1)
  })

  it('shows a failed job with its backend error and a retry action', async () => {
    const onRetry = vi.fn()
    renderStatus({
      job: job({
        status: 'failed',
        stage: 'ingesting',
        attempts: 3,
        error_code: 'ingestion_failed',
        error_detail: 'All 1 document(s) failed to ingest.',
      }),
      onRetry,
    })

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('Processing failed (ingestion failed)')
    expect(alert).toHaveTextContent('All 1 document(s) failed to ingest.')
    expect(getJob).not.toHaveBeenCalled()
    await userEvent.setup().click(screen.getByRole('button', { name: /Retry processing/ }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('explains a scheduled retry instead of reporting failure', () => {
    renderStatus({
      job: job({ attempts: 1, error_code: 'workflow_timeout', error_detail: 'Workflow timed out' }),
    })
    expect(screen.getByText(/A retry is scheduled/)).toHaveTextContent('Workflow timed out')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
