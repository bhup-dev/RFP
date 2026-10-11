import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { BidListContent } from '../pages/BidsPage'

function renderList(props) {
  return render(
    <MemoryRouter>
      <BidListContent {...props} />
    </MemoryRouter>,
  )
}

describe('bid list states', () => {
  it('renders loading state', () => {
    renderList({ loading: true })
    expect(screen.getByRole('status')).toHaveTextContent('Loading bids')
  })

  it('renders error state with retry', () => {
    const retry = vi.fn()
    renderList({ error: new Error('Backend unavailable'), onRetry: retry })
    expect(screen.getByRole('alert')).toHaveTextContent('Backend unavailable')
    screen.getByRole('button', { name: 'Try again' }).click()
    expect(retry).toHaveBeenCalledOnce()
  })

  it('renders empty state without inventing bids', () => {
    renderList({ bids: [] })
    expect(screen.getByText('No bids available')).toBeInTheDocument()
  })

  it('distinguishes an empty search from an empty dataset', () => {
    renderList({ bids: [], filtered: true })
    expect(screen.getByText('No matching bids')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /New bid/ })).not.toBeInTheDocument()
  })

  it('renders backend bids with document counts and processing status', () => {
    renderList({
      bids: [
        {
          id: 'bid-1',
          bid_identifier: 'Springfield-Docks',
          title: 'Dock repairs',
          solicitation_number: 'RFP-12',
          created_at: '2026-10-11T08:00:00Z',
          document_count: 3,
          indexed_document_count: 2,
          latest_job: { status: 'running' },
          latest_workflow_status: 'needs_review',
        },
        {
          id: 'bid-2',
          bid_identifier: 'Unprocessed',
          created_at: '2026-10-11T08:00:00Z',
          document_count: 0,
          indexed_document_count: 0,
          latest_job: null,
          latest_workflow_status: null,
        },
      ],
    })
    expect(screen.getByRole('link', { name: 'Dock repairs' })).toHaveAttribute('href', '/bids/bid-1')
    expect(screen.getByText(/Springfield-Docks · RFP-12/)).toBeInTheDocument()
    expect(screen.getByText('2/3 indexed')).toBeInTheDocument()
    expect(screen.getByText('running')).toBeInTheDocument()
    expect(screen.getByText('Workflow: needs review')).toBeInTheDocument()
    expect(screen.getByText('Not processed')).toBeInTheDocument()
  })
})
