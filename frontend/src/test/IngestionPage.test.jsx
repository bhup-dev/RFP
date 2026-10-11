import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import BidsPage from '../pages/BidsPage'
import { listBids } from '../services/bidService'
import { processDirectory, scanDirectory } from '../services/ingestionService'

vi.mock('../services/bidService', () => ({ listBids: vi.fn() }))
vi.mock('../services/ingestionService', () => ({
  processDirectory: vi.fn(),
  scanDirectory: vi.fn(),
}))

const scanResult = {
  files_discovered: 2,
  files_registered: 1,
  duplicates_skipped: 1,
  changed_files: 0,
  unsupported_files: 0,
  failed_files: 0,
  results: [
    {
      relative_path: 'Bid1/original.pdf',
      document_type: 'pdf',
      outcome: 'registered',
      document_id: 'doc-1',
      bid_identifier: 'Bid1',
    },
    {
      relative_path: 'Bid1/addendum.html',
      document_type: 'html',
      outcome: 'duplicate',
      document_id: 'doc-2',
      bid_identifier: 'Bid1',
    },
  ],
}

function renderPage() {
  return render(
    <MemoryRouter>
      <BidsPage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  listBids.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 })
  scanDirectory.mockResolvedValue(scanResult)
  processDirectory.mockResolvedValue({
    files_discovered: 1,
    documents_completed: 1,
    documents_failed: 0,
    documents_skipped: 0,
    chunks_created: 3,
    points_upserted: 3,
    results: [
      {
        relative_path: 'Bid1/original.pdf',
        bid_id: 'bid-1',
        document_id: 'doc-1',
        completed: true,
        parsing: 'completed',
        chunking: 'completed',
        indexing: 'completed',
        chunks_created: 3,
        errors: [],
      },
    ],
  })
})

describe('server-side ingestion page', () => {
  it('keeps server folder ingestion as an advanced option next to browser upload', async () => {
    renderPage()
    expect(screen.getByText('Advanced: server folder ingestion')).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: /New bid/ })[0]).toHaveAttribute('href', '/bids/new')
    expect(screen.getByText('PDF')).toBeInTheDocument()
    expect(screen.getByText('HTML')).toBeInTheDocument()
    expect(await screen.findByText('No bids available')).toBeInTheDocument()
  })

  it('renders backend scan results and filters by filename and status', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.type(screen.getByLabelText('Relative directory'), 'Bid1')
    await user.click(screen.getByRole('button', { name: 'Scan metadata' }))

    expect(await screen.findByText('original.pdf')).toBeInTheDocument()
    expect(screen.getByText('addendum.html')).toBeInTheDocument()
    expect(scanDirectory).toHaveBeenCalledWith(
      { relativeDirectory: 'Bid1', bidIdentifier: '' },
      expect.any(AbortSignal),
    )

    await user.type(screen.getByLabelText('Search results'), 'addendum')
    expect(screen.queryByText('original.pdf')).not.toBeInTheDocument()
    expect(screen.getByText('addendum.html')).toBeInTheDocument()

    await user.clear(screen.getByLabelText('Search results'))
    await user.selectOptions(screen.getByLabelText('Status'), 'registered')
    expect(screen.getByText('original.pdf')).toBeInTheDocument()
    expect(screen.queryByText('addendum.html')).not.toBeInTheDocument()
  })

  it('shows completion only after the process API confirms it', async () => {
    let resolveRequest
    processDirectory.mockReturnValue(new Promise((resolve) => { resolveRequest = resolve }))
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByRole('button', { name: 'Process & index' }))

    expect(screen.getByRole('status')).toHaveTextContent('Processing and indexing documents')
    expect(screen.queryByText('original.pdf')).not.toBeInTheDocument()

    resolveRequest({
      files_discovered: 1,
      documents_completed: 1,
      documents_failed: 0,
      documents_skipped: 0,
      results: [{ relative_path: 'Bid1/original.pdf', completed: true, errors: [] }],
    })
    expect(await screen.findByText('original.pdf')).toBeInTheDocument()
    expect(screen.getAllByText('completed')).toHaveLength(2)
  })

  it('shows backend failure and allows a retry without success state', async () => {
    scanDirectory.mockRejectedValueOnce(new Error('Qdrant unavailable'))
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByRole('button', { name: 'Scan metadata' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Qdrant unavailable')
    expect(screen.queryByText('original.pdf')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(scanDirectory).toHaveBeenCalledTimes(2))
    expect(await screen.findByText('original.pdf')).toBeInTheDocument()
  })
})
