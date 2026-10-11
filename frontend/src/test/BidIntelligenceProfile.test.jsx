import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useEffect } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PROFILE_FIELD_NAMES } from '../constants/profileFields'
import { ReviewAuthProvider } from '../context/ReviewAuthContext'
import { useReviewAuth } from '../context/useReviewAuth'
import BidDetailsPage from '../pages/BidDetailsPage'
import { getBid, getIntelligenceProfile, startProcessing } from '../services/bidService'
import { listReviews } from '../services/reviewService'

vi.mock('../services/bidService', async (importOriginal) => ({
  ...(await importOriginal()),
  getIntelligenceProfile: vi.fn(),
  getBid: vi.fn(),
  getJob: vi.fn(),
  startProcessing: vi.fn(),
}))

function bidDetail(overrides = {}) {
  return {
    id: 'bid-1',
    bid_identifier: 'Bid1',
    title: null,
    created_at: '2026-10-11T08:00:00Z',
    document_count: 1,
    indexed_document_count: 1,
    latest_job: null,
    latest_workflow_status: 'completed',
    documents: [
      {
        id: 'doc-1',
        bid_id: 'bid-1',
        file_name: 'abc_solicitation.pdf',
        original_filename: 'solicitation.pdf',
        document_type: 'pdf',
        document_role: 'original',
        ingestion_status: 'indexed',
        created_at: '2026-10-11T08:00:00Z',
      },
    ],
    jobs: [],
    ...overrides,
  }
}
vi.mock('../services/qaService', () => ({ askBid: vi.fn() }))
vi.mock('../services/reviewService', () => ({
  listReviews: vi.fn(),
}))

const citation = {
  bid_id: 'bid-1',
  document_id: 'doc-1',
  chunk_id: 'chunk-1',
  file_name: 'solicitation.pdf',
  page_number: 7,
  section_title: 'Submission requirements',
  excerpt: 'Responses must be received by 2:00 PM CST.',
  relevance_score: 0.91,
}

function profileFixture() {
  const fields = Object.fromEntries(
    PROFILE_FIELD_NAMES.map((name) => [
      name,
      {
        value: null,
        validation_status: 'not_found',
        confidence_score: null,
        supporting_citations: [],
        contradicting_citations: [],
        extraction_notes: null,
        review_recommendation: 'Review source documents.',
        addendum_changes: [],
      },
    ]),
  )
  fields['Bid Number'] = {
    ...fields['Bid Number'],
    value: 'JA-100',
    validation_status: 'validated',
    confidence_score: 0.93,
    supporting_citations: [citation],
    review_recommendation: 'No action required.',
  }
  fields.Product = {
    ...fields.Product,
    value: ['Laptop', 'Tablet'],
    validation_status: 'partially_supported',
  }
  fields.contact_info = {
    ...fields.contact_info,
    value: { name: 'Procurement Office', email: 'procurement@example.test' },
    validation_status: 'needs_review',
  }
  fields['Bid Summary'] = {
    ...fields['Bid Summary'],
    value: 'A long, source-grounded summary that remains fully readable without truncation.',
    validation_status: 'validated',
  }
  fields['Due Date'] = {
    ...fields['Due Date'],
    value: 'July 9 at 2:00 PM CST',
    validation_status: 'conflicting',
    supporting_citations: [citation],
    contradicting_citations: [{ ...citation, chunk_id: 'chunk-2', page_number: 9, excerpt: 'Responses close at 3:00 PM CST.' }],
    addendum_changes: [
      {
        finding_id: 'finding-1',
        analysis_run_id: 'addendum-run',
        classification: 'conflict',
        topic: 'Submission deadline',
        original_value: '2:00 PM CST',
        amended_value: '3:00 PM CST',
        explanation: 'The addendum states a different closing time.',
        severity: 'high',
        original_sources: [citation],
        addendum_sources: [{ ...citation, chunk_id: 'chunk-2', file_name: 'addendum.pdf' }],
      },
    ],
  }
  return {
    metadata: {
      bid_id: 'bid-1',
      bid_identifier: 'Bid1',
      solicitation_number: 'JA-100',
      profile_id: 'profile-1',
      profile_version: 1,
      generated_at: '2026-10-11T00:00:00Z',
      extraction_run_id: 'extract-1',
      validation_run_id: 'validate-1',
      addendum_analysis_run_id: 'addendum-run',
      source_document_count: 2,
      source_document_ids: ['doc-1', 'doc-2'],
      status: 'needs_review',
      review_required: true,
      warnings: [],
      unresolved_issues: ['Submission deadline conflict'],
    },
    fields,
  }
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/bids/bid-1']}>
      <Routes>
        <Route path="/bids/:bidId" element={<BidDetailsPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

function SeededReviewer({ children }) {
  const { auth, setCredentials } = useReviewAuth()
  useEffect(() => {
    if (!auth) setCredentials('token', 'reviewer')
  }, [auth, setCredentials])
  return auth ? children : null
}

function renderAuthenticatedPage() {
  return render(
    <MemoryRouter initialEntries={['/bids/bid-1']}>
      <ReviewAuthProvider>
        <SeededReviewer>
          <Routes>
            <Route path="/bids/:bidId" element={<BidDetailsPage />} />
          </Routes>
        </SeededReviewer>
      </ReviewAuthProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  getIntelligenceProfile.mockResolvedValue(profileFixture())
  getBid.mockResolvedValue(bidDetail())
  listReviews.mockResolvedValue([])
})

describe('bid intelligence profile', () => {
  it('loads by bid ID and renders all exact required fields', async () => {
    renderPage()
    expect(await screen.findByText('20 of 20 fields shown')).toBeInTheDocument()
    expect(getIntelligenceProfile).toHaveBeenCalledWith('bid-1', expect.objectContaining({ signal: expect.any(AbortSignal) }))
    for (const name of PROFILE_FIELD_NAMES) {
      expect(screen.getByRole('heading', { name, level: 3 })).toBeInTheDocument()
    }
    expect(document.querySelectorAll('.intelligence-field')).toHaveLength(20)
  })

  it('renders missing, array, object, long text, status, and confidence accurately', async () => {
    renderPage()
    await screen.findByText('20 of 20 fields shown')
    expect(screen.getAllByText('Not found in source').length).toBeGreaterThan(0)
    expect(screen.getByText('Laptop')).toBeInTheDocument()
    expect(screen.getByText('Procurement Office')).toBeInTheDocument()
    expect(screen.getByText(/long, source-grounded summary/)).toBeInTheDocument()
    expect(screen.getByText('Confidence 93%')).toBeInTheDocument()
    expect(screen.queryByText('Confidence 0%')).not.toBeInTheDocument()
    expect(screen.getAllByText('conflicting').length).toBeGreaterThan(0)
  })

  it('opens and closes traceable citation evidence', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('20 of 20 fields shown')
    const bidNumber = screen.getByRole('heading', { name: 'Bid Number', level: 3 }).closest('article')
    const toggle = within(bidNumber).getByRole('button', { name: 'Inspect evidence (1)' })
    await user.click(toggle)
    expect(within(bidNumber).getByText('solicitation.pdf')).toBeInTheDocument()
    expect(within(bidNumber).getByText('Page 7 · Submission requirements')).toBeInTheDocument()
    expect(within(bidNumber).getByText('chunk-1')).toBeInTheDocument()
    expect(within(bidNumber).queryByText(/does not prove/i)).not.toBeInTheDocument()
    await user.click(within(bidNumber).getByRole('button', { name: 'Close evidence' }))
    expect(within(bidNumber).queryByText('solicitation.pdf')).not.toBeInTheDocument()
  })

  it('searches field names and filters backend validation statuses', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('20 of 20 fields shown')
    await user.type(screen.getByLabelText('Search field names'), 'product')
    expect(screen.getByText('2 of 20 fields shown')).toBeInTheDocument()
    await user.clear(screen.getByLabelText('Search field names'))
    await user.selectOptions(screen.getByLabelText('Validation status'), 'conflicting')
    expect(screen.getByText('1 of 20 fields shown')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Due Date', level: 3 })).toBeInTheDocument()
  })

  it('preserves original and amended addendum values and highlights conflicts', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('20 of 20 fields shown')
    await user.click(screen.getByRole('button', { name: /Addendum changes/ }))
    expect(screen.getByText('Original RFP')).toBeInTheDocument()
    expect(screen.getByText('2:00 PM CST')).toBeInTheDocument()
    expect(screen.getByText('Addendum statement')).toBeInTheDocument()
    expect(screen.getByText('3:00 PM CST')).toBeInTheDocument()
    expect(screen.getByText(/remains unresolved/)).toBeInTheDocument()
    expect(screen.getByText('addendum.pdf')).toBeInTheDocument()
  })

  it('offers grounded Q&A next to the profile', async () => {
    renderPage()
    expect(await screen.findByRole('region', { name: 'Ask about this bid' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Ask questions/ })).toBeInTheDocument()
    expect(screen.getByLabelText('Your question')).toBeEnabled()
  })

  it('offers processing for an existing bid that has no profile yet', async () => {
    const user = userEvent.setup()
    getIntelligenceProfile.mockRejectedValue(Object.assign(new Error('Not found'), { status: 404 }))
    startProcessing.mockResolvedValue({
      id: 'job-1',
      bid_id: 'bid-1',
      job_type: 'ingest_and_analyze',
      status: 'queued',
      stage: 'queued',
      attempts: 0,
      max_attempts: 3,
    })
    renderPage()

    expect(await screen.findByText('Not processed yet')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByText('solicitation.pdf')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Process & analyze/ }))
    expect(startProcessing).toHaveBeenCalledWith('bid-1')
    expect(await screen.findByRole('region', { name: 'Processing status' })).toBeInTheDocument()
  })

  it('shows API failure and retries without triggering extraction', async () => {
    getIntelligenceProfile.mockRejectedValueOnce(Object.assign(new Error('Profile unavailable'), { status: 503 }))
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent('Profile unavailable')
    expect(getIntelligenceProfile).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(getIntelligenceProfile).toHaveBeenCalledTimes(2))
    expect(await screen.findByText('20 of 20 fields shown')).toBeInTheDocument()
  })

  it('links authenticated field-level reviews without replacing extracted values', async () => {
    listReviews.mockResolvedValue([
      {
        review_item_id: 'review-payment',
        field_name: 'Payment Terms',
        status: 'pending',
        category: 'unsupported_claim',
      },
    ])
    renderAuthenticatedPage()
    const field = (await screen.findByRole('heading', { name: 'Payment Terms', level: 3 })).closest('article')
    expect(within(field).getByText('Not found in source')).toBeInTheDocument()
    expect(within(field).getByRole('link', { name: 'pending · unsupported claim' })).toHaveAttribute(
      'href',
      '/reviews/review-payment',
    )
    expect(listReviews).toHaveBeenCalledWith(
      { bid_id: 'bid-1' },
      { token: 'token', reviewerId: 'reviewer' },
      expect.any(AbortSignal),
    )
  })
})
