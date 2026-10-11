import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ReviewAuthProvider } from '../context/ReviewAuthContext'
import ReviewDetailPage from '../pages/ReviewDetailPage'
import {
  getReview,
  getWorkflowReviewStatus,
  submitReviewDecision,
} from '../services/reviewService'

vi.mock('../services/reviewService', () => ({
  getReview: vi.fn(),
  getWorkflowReviewStatus: vi.fn(),
  submitReviewDecision: vi.fn(),
}))

const citation = {
  bid_id: 'bid-1',
  document_id: 'document-1',
  chunk_id: 'chunk-1',
  file_name: 'terms.pdf',
  page_number: 12,
  section_title: 'Payment',
  excerpt: 'Payment shall be made within thirty days.',
  relevance_score: 0.94,
}

const pendingItem = {
  review_item_id: 'review-1',
  operation_type: 'bid_workflow',
  bid_id: 'bid-1',
  workflow_id: 'workflow-1',
  source_stage: 'validate_extraction',
  category: 'unsupported_claim',
  item_reference: 'field:Payment Terms',
  field_name: 'Payment Terms',
  proposed_value: 'Net 45',
  validation_status: 'unsupported',
  confidence_score: 0.55,
  supporting_citations: [citation],
  contradicting_citations: [],
  reason: 'The proposed term is not fully supported.',
  severity: 'high',
  status: 'pending',
  created_at: '2026-10-11T00:00:00Z',
  reviewer_id: null,
  decision_at: null,
  corrected_value: null,
  reevaluation_attempts: 0,
  decisions: [],
}

const workflowStatus = {
  workflow_id: 'workflow-1',
  total: 1,
  pending: 1,
  approved: 0,
  rejected: 0,
  unresolved: 0,
  reevaluation_requested: 0,
  all_required_resolved: false,
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/reviews/review-1']}>
      <ReviewAuthProvider>
        <Routes>
          <Route path="/reviews/:reviewId" element={<ReviewDetailPage />} />
        </Routes>
      </ReviewAuthProvider>
    </MemoryRouter>,
  )
}

async function login(user) {
  await user.type(screen.getByLabelText('Reviewer ID'), 'reviewer-7')
  await user.type(screen.getByLabelText('Review API token'), 'secret-token')
  await user.click(screen.getByRole('button', { name: 'Connect to review queue' }))
}

beforeEach(() => {
  vi.clearAllMocks()
  getReview.mockResolvedValue(pendingItem)
  getWorkflowReviewStatus.mockResolvedValue(workflowStatus)
})

describe('Review detail and decisions', () => {
  it('renders proposal, bid-scoped evidence, workflow aggregate, and supported actions', async () => {
    const user = userEvent.setup()
    renderPage()
    await login(user)
    expect(await screen.findByRole('heading', { name: 'Original proposal' })).toBeInTheDocument()
    expect(screen.getByText('Net 45')).toBeInTheDocument()
    expect(screen.getByText('terms.pdf')).toBeInTheDocument()
    expect(screen.getAllByText('bid-1').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: 'Approve finding' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reject finding' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Request re-evaluation' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Mark unresolved' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Open related profile evidence/ })).toHaveAttribute(
      'href',
      '/bids/bid-1#field-payment-terms',
    )
  })

  it('requires confirmation and comments, submits the exact payload, then refreshes', async () => {
    let resolveDecision
    submitReviewDecision.mockImplementation(
      () => new Promise((resolve) => { resolveDecision = resolve }),
    )
    const decision = {
      decision_id: 'decision-1',
      review_item_id: 'review-1',
      decision: 'reject',
      previous_status: 'pending',
      new_status: 'rejected',
      reviewer_id: 'reviewer-7',
      comments: 'The source supports Net 30 instead.',
      corrected_value: 'Net 30',
      result_reference: null,
      created_at: '2026-10-11T01:00:00Z',
      duplicate: false,
    }
    getReview
      .mockResolvedValueOnce(pendingItem)
      .mockResolvedValue({ ...pendingItem, status: 'rejected', corrected_value: 'Net 30', decisions: [decision] })
    const user = userEvent.setup()
    renderPage()
    await login(user)
    await screen.findByRole('button', { name: 'Reject finding' })
    await user.click(screen.getByRole('button', { name: 'Reject finding' }))
    const dialog = screen.getByRole('dialog', { name: 'Reject finding' })
    await user.click(within(dialog).getByRole('button', { name: 'Confirm reject finding' }))
    expect(within(dialog).getByRole('alert')).toHaveTextContent('comments are required')
    await user.click(within(dialog).getByLabelText('Supply a human-corrected value'))
    await user.clear(within(dialog).getByLabelText('Human-corrected value'))
    await user.type(within(dialog).getByLabelText('Human-corrected value'), 'Net 30')
    await user.type(
      within(dialog).getByLabelText('Reviewer comments (required)'),
      'The source supports Net 30 instead.',
    )
    await user.click(within(dialog).getByRole('button', { name: 'Confirm reject finding' }))
    expect(submitReviewDecision).toHaveBeenCalledWith(
      'review-1',
      {
        decision: 'reject',
        idempotency_key: expect.any(String),
        comments: 'The source supports Net 30 instead.',
        corrected_value: 'Net 30',
      },
      { token: 'secret-token', reviewerId: 'reviewer-7' },
    )
    expect(within(dialog).getByRole('button', { name: 'Submitting decision…' })).toBeDisabled()
    expect(screen.getAllByText('pending').length).toBeGreaterThan(0)
    resolveDecision({
      item: { ...pendingItem, status: 'rejected' },
      decision,
      workflow_resumed: true,
      workflow_status: 'completed',
      detail: null,
    })
    expect(await screen.findByText('Decision persisted by the backend')).toBeInTheDocument()
    expect(screen.getByText(/Workflow resume was confirmed.*completed/i)).toBeInTheDocument()
    expect(await screen.findByText('Persisted human-corrected value')).toBeInTheDocument()
    expect(getReview).toHaveBeenCalledTimes(2)
  })

  it('does not claim workflow completion when a persisted decision did not resume it', async () => {
    submitReviewDecision.mockResolvedValue({
      item: { ...pendingItem, status: 'unresolved' },
      decision: {
        decision_id: 'decision-2',
        review_item_id: 'review-1',
        decision: 'mark_unresolved',
        previous_status: 'pending',
        new_status: 'unresolved',
        reviewer_id: 'reviewer-7',
        comments: 'Evidence remains ambiguous.',
        corrected_value: null,
        result_reference: null,
        created_at: '2026-10-11T01:00:00Z',
        duplicate: false,
      },
      workflow_resumed: false,
      workflow_status: 'pending_review',
      detail: null,
    })
    getReview
      .mockResolvedValueOnce(pendingItem)
      .mockResolvedValue({ ...pendingItem, status: 'unresolved' })
    const user = userEvent.setup()
    renderPage()
    await login(user)
    await screen.findByRole('button', { name: 'Mark unresolved' })
    await user.click(screen.getByRole('button', { name: 'Mark unresolved' }))
    await user.type(screen.getByLabelText('Reviewer comments (required)'), 'Evidence remains ambiguous.')
    await user.click(screen.getByRole('button', { name: 'Confirm mark unresolved' }))
    expect(await screen.findByText(/Workflow was not resumed.*pending_review/i)).toBeInTheDocument()
    expect(screen.queryByText(/Workflow resume was confirmed/i)).not.toBeInTheDocument()
  })

  it('shows decision failure without changing the pending item', async () => {
    submitReviewDecision.mockRejectedValue(new Error('workflow checkpoint is missing'))
    const user = userEvent.setup()
    renderPage()
    await login(user)
    await screen.findByRole('button', { name: 'Reject finding' })
    await user.click(screen.getByRole('button', { name: 'Reject finding' }))
    await user.type(screen.getByLabelText('Reviewer comments (required)'), 'Reject with evidence.')
    await user.click(screen.getByRole('button', { name: 'Confirm reject finding' }))
    expect(await screen.findByText('Decision was not accepted')).toBeInTheDocument()
    expect(screen.getByText('workflow checkpoint is missing')).toBeInTheDocument()
    expect(screen.getAllByText('pending').length).toBeGreaterThan(0)
  })

  it('renders resolved history without presenting transition actions', async () => {
    getReview.mockResolvedValue({
      ...pendingItem,
      status: 'approved',
      reviewer_id: 'reviewer-7',
      corrected_value: 'Net 30',
      decisions: [
        {
          decision_id: 'decision-3',
          review_item_id: 'review-1',
          decision: 'approve',
          previous_status: 'pending',
          new_status: 'approved',
          reviewer_id: 'reviewer-7',
          comments: 'Confirmed after review.',
          corrected_value: 'Net 30',
          result_reference: null,
          created_at: '2026-10-11T01:00:00Z',
          duplicate: false,
        },
      ],
    })
    const user = userEvent.setup()
    renderPage()
    await login(user)
    expect(await screen.findByText('Confirmed after review.')).toBeInTheDocument()
    expect(screen.getByText(/backend permits decisions only for pending items/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve finding' })).not.toBeInTheDocument()
    expect(screen.getAllByText('Human-corrected value').length).toBeGreaterThan(0)
  })

  it('handles protected detail authorization errors', async () => {
    getReview.mockRejectedValue(Object.assign(new Error('reviewer authorization failed'), { status: 403 }))
    const user = userEvent.setup()
    renderPage()
    await login(user)
    expect(await screen.findByText('Reviewer authorization failed')).toBeInTheDocument()
    expect(submitReviewDecision).not.toHaveBeenCalled()
  })
})
