import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import BidOverview from '../components/bids/BidOverview'
import CitationList from '../components/intelligence/CitationList'
import { startProcessing } from '../services/bidService'

vi.mock('../services/bidService', async (importOriginal) => ({
  ...(await importOriginal()),
  getJob: vi.fn(),
  startProcessing: vi.fn(),
}))

function bid(overrides = {}) {
  return {
    id: 'bid-1',
    bid_identifier: 'Docks',
    documents: [
      {
        id: 'doc-1',
        file_name: 'a1b2_rfp.pdf',
        original_filename: 'rfp.pdf',
        document_type: 'pdf',
        document_role: 'original',
        ingestion_status: 'indexed',
        created_at: '2026-10-11T08:00:00Z',
      },
      {
        id: 'doc-2',
        file_name: 'c3d4_addendum.html',
        original_filename: 'addendum.html',
        document_type: 'html',
        document_role: 'addendum',
        addendum_number: 1,
        ingestion_status: 'pending',
        created_at: '2026-10-11T08:00:00Z',
      },
    ],
    jobs: [],
    ...overrides,
  }
}

function renderOverview(props) {
  return render(
    <MemoryRouter>
      <BidOverview bidId="bid-1" {...props} />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('bid documents and processing', () => {
  it('lists stored documents with open and download links', () => {
    renderOverview({ bid: bid() })
    const list = screen.getByRole('list', { name: 'Source documents' })
    expect(within(list).getByText('rfp.pdf')).toBeInTheDocument()
    expect(within(list).getByText(/HTML · addendum #1/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open rfp.pdf' })).toHaveAttribute('href', '/api/v1/documents/doc-1/file')
    expect(screen.queryByRole('link', { name: 'Open addendum.html' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Download addendum.html' })).toHaveAttribute(
      'href',
      '/api/v1/documents/doc-2/file?download=true',
    )
  })

  it('disables processing without documents and while a job is active', () => {
    const { unmount } = renderOverview({ bid: bid({ documents: [] }) })
    expect(screen.getByText('No documents yet')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Process & analyze/ })).toBeDisabled()
    unmount()

    renderOverview({
      bid: bid({
        jobs: [{ id: 'job-1', bid_id: 'bid-1', status: 'running', stage: 'ingesting', attempts: 1, max_attempts: 3 }],
      }),
    })
    expect(screen.getByRole('button', { name: /Process & analyze/ })).toBeDisabled()
    expect(screen.getByRole('region', { name: 'Processing status' })).toBeInTheDocument()
  })

  it('queues processing and shows the new job', async () => {
    startProcessing.mockResolvedValue({
      id: 'job-2',
      bid_id: 'bid-1',
      job_type: 'ingest_and_analyze',
      status: 'queued',
      stage: 'queued',
      attempts: 0,
      max_attempts: 3,
    })
    renderOverview({ bid: bid() })
    await userEvent.setup().click(screen.getByRole('button', { name: /Process & analyze/ }))
    expect(startProcessing).toHaveBeenCalledWith('bid-1')
    expect(await screen.findByRole('region', { name: 'Processing status' })).toBeInTheDocument()
  })

  it('links citations to the exact source page', () => {
    render(
      <CitationList
        citations={[
          {
            document_id: 'doc-1',
            chunk_id: 'chunk-1',
            file_name: 'rfp.pdf',
            page_number: 7,
            excerpt: 'Responses are due Friday.',
          },
        ]}
      />,
    )
    expect(screen.getByRole('link', { name: /Open source document at page 7/ })).toHaveAttribute(
      'href',
      '/api/v1/documents/doc-1/file#page=7',
    )
  })
})
