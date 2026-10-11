import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ComparisonPage from '../pages/ComparisonPage'
import { getIntelligenceProfile, listBids } from '../services/bidService'
import { compareBids } from '../services/comparisonService'

vi.mock('../services/bidService', async (importOriginal) => ({
  ...(await importOriginal()),
  getIntelligenceProfile: vi.fn(),
  listBids: vi.fn(),
}))
vi.mock('../services/comparisonService', () => ({ compareBids: vi.fn() }))

const BID_A = '11111111-1111-4111-8111-111111111111'
const BID_B = '22222222-2222-4222-8222-222222222222'

function profile(bidId, identifier, title) {
  return {
    metadata: {
      bid_id: bidId,
      bid_identifier: identifier,
      solicitation_number: `SOL-${identifier}`,
      status: 'ready',
      source_document_count: 2,
    },
    fields: { Title: { value: title } },
  }
}

function citation(bidId, suffix) {
  return {
    bid_id: bidId,
    document_id: `document-${suffix}`,
    chunk_id: `chunk-${suffix}`,
    file_name: `source-${suffix}.pdf`,
    page_number: 4,
    section_title: 'Requirements',
    excerpt: `Evidence excerpt for ${suffix}`,
    relevance_score: 0.9,
  }
}

function comparisonReport() {
  const categories = [
    ['scope', 'Title', 'equivalent'],
    ['products', 'Product', 'different'],
    ['commercial_terms', 'Payment Terms', 'missing'],
    ['commercial_terms', 'Due Date', 'conflicting'],
    ['compliance', 'MFG for Registration', 'not_comparable'],
    ['addenda', 'Installation', 'uncertain'],
  ]
  return {
    comparison_id: 'comparison-1',
    status: 'completed_with_warnings',
    bid_ids: [BID_A, BID_B],
    profile_references: [
      { bid_id: BID_A, bid_identifier: 'Alpha', profile_id: 'profile-a', profile_status: 'ready' },
      { bid_id: BID_B, bid_identifier: 'Beta', profile_id: 'profile-b', profile_status: 'ready' },
    ],
    comparison_dimensions: ['scope', 'products', 'commercial_terms', 'compliance', 'addenda'],
    summary: {
      total_rows: 6,
      equivalent: 1,
      different: 1,
      missing: 1,
      conflicting: 1,
      not_comparable: 1,
      uncertain: 1,
    },
    comparison_rows: categories.map(([dimension, attribute, classification], index) => ({
      dimension,
      attribute,
      classification,
      explanation: `${attribute} was classified as ${classification}.`,
      confidence: classification === 'missing' ? null : 0.8,
      requires_human_review: ['conflicting', 'uncertain'].includes(classification),
      review_recommendation: 'Review the returned evidence.',
      values: [
        {
          bid_id: BID_A,
          original_value: classification === 'missing' ? null : `Alpha ${attribute}`,
          normalized_value: null,
          field_status: classification === 'conflicting' ? 'conflicting' : 'validated',
          supporting_citations: index === 3 ? [citation(BID_A, 'alpha')] : [],
          contradicting_citations: [],
          addendum_changes: [],
        },
        {
          bid_id: BID_B,
          original_value: `Beta ${attribute}`,
          normalized_value: null,
          field_status: 'validated',
          supporting_citations: index === 3 ? [citation(BID_B, 'beta')] : [],
          contradicting_citations: [],
          addendum_changes: [],
        },
      ],
    })),
    key_similarities: ['Titles describe equivalent scope.'],
    key_differences: ['Product requirements differ.'],
    missing_information: ['Alpha payment terms were not found.'],
    unresolved_conflicts: ['Alpha due date has conflicting evidence.'],
    warnings: ['One or more rows require review.'],
    errors: [],
    requires_human_review: true,
    created_at: '2026-10-11T00:00:00Z',
  }
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ComparisonPage />
    </MemoryRouter>,
  )
}

async function addBid(user, id) {
  const input = screen.getByLabelText('Bid UUID')
  await user.clear(input)
  await user.type(input, id)
  await user.click(screen.getByRole('button', { name: 'Add bid' }))
}

async function selectTwoBids(user) {
  getIntelligenceProfile
    .mockResolvedValueOnce(profile(BID_A, 'Alpha', 'Alpha procurement'))
    .mockResolvedValueOnce(profile(BID_B, 'Beta', 'Beta procurement'))
  await addBid(user, BID_A)
  expect(await screen.findByText('Alpha procurement')).toBeInTheDocument()
  await addBid(user, BID_B)
  expect(await screen.findByText('Beta procurement')).toBeInTheDocument()
}

beforeEach(() => {
  vi.clearAllMocks()
  compareBids.mockResolvedValue(comparisonReport())
  listBids.mockResolvedValue({ items: [], total: 0, limit: 100, offset: 0 })
})

describe('Bid Comparison page', () => {
  it('verifies stable bid IDs, prevents duplicates, and requires two bids', async () => {
    const user = userEvent.setup()
    getIntelligenceProfile.mockResolvedValue(profile(BID_A, 'Alpha', 'Alpha procurement'))
    renderPage()
    expect(screen.getByText(/Choose stored bids or paste a bid ID/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Compare selected bids' })).toBeDisabled()
    await addBid(user, BID_A)
    expect(await screen.findByText('Alpha procurement')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Compare selected bids' })).toBeDisabled()
    await addBid(user, BID_A)
    expect(await screen.findByRole('alert')).toHaveTextContent('already selected')
    expect(getIntelligenceProfile).toHaveBeenCalledTimes(1)
  })

  it('submits the exact synchronous comparison request after two distinct bids', async () => {
    const user = userEvent.setup()
    renderPage()
    await selectTwoBids(user)
    await user.click(screen.getByRole('button', { name: 'Compare selected bids' }))
    await waitFor(() => expect(compareBids).toHaveBeenCalledTimes(1))
    expect(compareBids).toHaveBeenCalledWith(
      [BID_A, BID_B],
      expect.objectContaining({
        dimensions: ['scope', 'products', 'commercial_terms', 'compliance', 'addenda'],
        allowIncompleteProfiles: false,
        signal: expect.any(AbortSignal),
      }),
    )
    expect(await screen.findByRole('heading', { name: 'Comparison summary' })).toBeInTheDocument()
    expect(screen.queryByText(/winning bid|recommended winner/i)).not.toBeInTheDocument()
  })

  it('renders every backend category, missing values, and profile navigation', async () => {
    const user = userEvent.setup()
    renderPage()
    await selectTwoBids(user)
    await user.click(screen.getByRole('button', { name: 'Compare selected bids' }))
    await screen.findByRole('heading', { name: 'Detailed comparison' })
    for (const category of ['equivalent', 'different', 'missing', 'conflicting', 'not comparable', 'uncertain']) {
      expect(screen.getAllByText(category).length).toBeGreaterThan(0)
    }
    expect(screen.getAllByText('Not found in source').length).toBeGreaterThan(0)
    const links = screen.getAllByRole('link', { name: /Open Alpha profile|Profile/ })
    expect(links.some((link) => link.getAttribute('href') === `/bids/${BID_A}`)).toBe(true)
  })

  it('filters rows without changing the report', async () => {
    const user = userEvent.setup()
    renderPage()
    await selectTwoBids(user)
    await user.click(screen.getByRole('button', { name: 'Compare selected bids' }))
    await screen.findByRole('heading', { name: 'Detailed comparison' })
    await user.click(screen.getByRole('button', { name: 'conflicting 1' }))
    expect(screen.getByRole('rowheader', { name: /Due Date/ })).toBeInTheDocument()
    expect(screen.queryByRole('rowheader', { name: /Product was classified/ })).not.toBeInTheDocument()
    expect(compareBids).toHaveBeenCalledTimes(1)
  })

  it('opens bid-scoped supporting evidence for a conflict', async () => {
    const user = userEvent.setup()
    renderPage()
    await selectTwoBids(user)
    await user.click(screen.getByRole('button', { name: 'Compare selected bids' }))
    await screen.findByRole('heading', { name: 'Detailed comparison' })
    const dueDateRow = screen.getByRole('rowheader', { name: /Due Date/ }).closest('tr')
    const alphaCell = within(dueDateRow).getAllByRole('cell')[0]
    await user.click(within(alphaCell).getByRole('button', { name: 'Evidence (1)' }))
    expect(within(alphaCell).getByText('source-alpha.pdf')).toBeInTheDocument()
    expect(within(alphaCell).getByText(BID_A)).toBeInTheDocument()
    expect(within(alphaCell).queryByText('source-beta.pdf')).not.toBeInTheDocument()
  })

  it('disables duplicate submissions while comparison is pending', async () => {
    let resolveComparison
    compareBids.mockImplementation(
      () => new Promise((resolve) => { resolveComparison = resolve }),
    )
    const user = userEvent.setup()
    renderPage()
    await selectTwoBids(user)
    await user.click(screen.getByRole('button', { name: 'Compare selected bids' }))
    const pendingButton = screen.getByRole('button', { name: 'Comparing bids…' })
    expect(pendingButton).toBeDisabled()
    await user.click(pendingButton)
    expect(compareBids).toHaveBeenCalledTimes(1)
    resolveComparison(comparisonReport())
    expect(await screen.findByRole('heading', { name: 'Comparison summary' })).toBeInTheDocument()
  })

  it('preserves selected bids after failure and retries the comparison', async () => {
    const user = userEvent.setup()
    compareBids
      .mockRejectedValueOnce(new Error('Comparison service unavailable'))
      .mockResolvedValueOnce(comparisonReport())
    renderPage()
    await selectTwoBids(user)
    await user.click(screen.getByRole('button', { name: 'Compare selected bids' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Comparison service unavailable')
    expect(screen.getByText('Alpha procurement')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('heading', { name: 'Comparison summary' })).toBeInTheDocument()
    expect(compareBids).toHaveBeenCalledTimes(2)
  })

  it('fills the bid ID from the stored bid picker', async () => {
    const user = userEvent.setup()
    listBids.mockResolvedValue({
      items: [{ id: BID_A, bid_identifier: 'alpha', title: 'Alpha procurement' }],
      total: 1,
      limit: 100,
      offset: 0,
    })
    renderPage()
    const picker = await screen.findByLabelText('Stored bid')
    await user.selectOptions(picker, BID_A)
    expect(screen.getByLabelText('Bid UUID')).toHaveValue(BID_A)
  })
})
