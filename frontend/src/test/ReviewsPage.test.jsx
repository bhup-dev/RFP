import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ReviewAuthProvider } from '../context/ReviewAuthContext'
import ReviewsPage from '../pages/ReviewsPage'
import { listReviews } from '../services/reviewService'

vi.mock('../services/reviewService', () => ({ listReviews: vi.fn() }))

const reviewItem = {
  review_item_id: 'review-1',
  operation_type: 'bid_workflow',
  bid_id: 'bid-1',
  workflow_id: 'workflow-1',
  source_stage: 'validate_extraction',
  category: 'unsupported_claim',
  item_reference: 'field:Payment Terms',
  field_name: 'Payment Terms',
  proposed_value: 'Net 30',
  validation_status: 'unsupported',
  confidence_score: 0.62,
  supporting_citations: [],
  contradicting_citations: [],
  reason: 'No validated citation supports this term.',
  severity: 'high',
  status: 'pending',
  created_at: '2026-10-11T00:00:00Z',
  reviewer_id: null,
  decision_at: null,
  corrected_value: null,
  reevaluation_attempts: 0,
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ReviewAuthProvider>
        <ReviewsPage />
      </ReviewAuthProvider>
    </MemoryRouter>,
  )
}

async function login(user) {
  await user.type(screen.getByLabelText('Reviewer ID'), 'reviewer@example.test')
  await user.type(screen.getByLabelText('Review API token'), 'test-token')
  await user.click(screen.getByRole('button', { name: 'Connect to review queue' }))
}

beforeEach(() => {
  vi.clearAllMocks()
  listReviews.mockResolvedValue([reviewItem])
})

describe('Human Review queue', () => {
  it('requires an in-memory reviewer session and loads actual queue records', async () => {
    const user = userEvent.setup()
    renderPage()
    expect(listReviews).not.toHaveBeenCalled()
    await login(user)
    expect(await screen.findByRole('heading', { name: 'Payment Terms' })).toBeInTheDocument()
    expect(screen.getByText('Net 30')).toBeInTheDocument()
    expect(screen.getByText(/No validated citation/)).toBeInTheDocument()
    expect(listReviews).toHaveBeenCalledWith(
      { status: '', bid_id: '' },
      { token: 'test-token', reviewerId: 'reviewer@example.test' },
      expect.any(AbortSignal),
    )
  })

  it('renders an honest empty queue', async () => {
    listReviews.mockResolvedValue([])
    const user = userEvent.setup()
    renderPage()
    await login(user)
    expect(await screen.findByText('Review queue is empty')).toBeInTheDocument()
  })

  it('handles authorization failure and retries without losing credentials', async () => {
    listReviews
      .mockRejectedValueOnce(Object.assign(new Error('reviewer authorization failed'), { status: 403 }))
      .mockResolvedValueOnce([reviewItem])
    const user = userEvent.setup()
    renderPage()
    await login(user)
    expect(await screen.findByText('Reviewer authorization failed')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('heading', { name: 'Payment Terms' })).toBeInTheDocument()
    expect(listReviews).toHaveBeenCalledTimes(2)
  })

  it('applies server-supported status and bid filters and client category filtering', async () => {
    const user = userEvent.setup()
    renderPage()
    await login(user)
    await screen.findByRole('heading', { name: 'Payment Terms' })
    await user.selectOptions(screen.getByLabelText('Status'), 'pending')
    await waitFor(() =>
      expect(listReviews).toHaveBeenLastCalledWith(
        { status: 'pending', bid_id: '' },
        expect.any(Object),
        expect.any(AbortSignal),
      ),
    )
    await user.type(screen.getByLabelText('Bid UUID'), 'bid-1')
    await user.click(screen.getByRole('button', { name: 'Apply' }))
    await waitFor(() =>
      expect(listReviews).toHaveBeenLastCalledWith(
        { status: 'pending', bid_id: 'bid-1' },
        expect.any(Object),
        expect.any(AbortSignal),
      ),
    )
    await user.selectOptions(screen.getByLabelText('Category'), 'invalid_citation')
    expect(screen.getByText('No reviews match these filters')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(screen.getByLabelText('Category')).toHaveValue('')
  })
})
