import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import BidChat from '../components/bids/BidChat'
import { listBids } from '../services/bidService'
import { askBid } from '../services/qaService'

vi.mock('../services/qaService', () => ({ askBid: vi.fn() }))
vi.mock('../services/bidService', async (importOriginal) => ({
  ...(await importOriginal()),
  listBids: vi.fn(),
}))

function answer(overrides = {}) {
  return {
    bid_id: 'bid-1',
    question: 'When are proposals due?',
    answer: 'Proposals are due October 30, 2026 at 2:00 PM [S1].',
    found: true,
    sources: [
      {
        source_id: 'S1',
        chunk_id: 'chunk-1',
        document_id: 'doc-1',
        file_name: 'rfp.pdf',
        page_number: 4,
        section_title: 'Submission',
        excerpt: 'Proposals must be received by October 30, 2026 at 2:00 PM.',
      },
    ],
    warnings: [],
    ...overrides,
  }
}

function renderChat(props = {}) {
  return render(
    <MemoryRouter>
      <BidChat bidId="bid-1" indexedCount={2} {...props} />
    </MemoryRouter>,
  )
}

async function ask(user, question) {
  await user.type(screen.getByLabelText('Your question'), question)
  await user.keyboard('{Enter}')
}

beforeEach(() => {
  vi.clearAllMocks()
  listBids.mockResolvedValue({ items: [] })
})

describe('bid chat', () => {
  it('renders a grounded answer with citation chips and page-anchored sources', async () => {
    const user = userEvent.setup()
    askBid.mockResolvedValue(answer())
    renderChat()

    await ask(user, 'When are proposals due?')

    expect(askBid).toHaveBeenCalledWith(
      'bid-1',
      { question: 'When are proposals due?', history: [], compareBidIds: [] },
      expect.any(AbortSignal),
    )
    expect(await screen.findByText(/Proposals are due October 30, 2026/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'S1' })).toHaveAttribute('href', expect.stringMatching(/#m\d+-S1$/))
    const sources = screen.getByRole('list', { name: 'Answer sources' })
    expect(within(sources).getByText('rfp.pdf')).toBeInTheDocument()
    expect(within(sources).getByRole('link', { name: /Open at page 4/ })).toHaveAttribute(
      'href',
      '/api/v1/documents/doc-1/file#page=4',
    )
  })

  it('sends earlier turns as history for follow-up questions', async () => {
    const user = userEvent.setup()
    askBid.mockResolvedValueOnce(answer()).mockResolvedValueOnce(answer({ answer: 'Bond is 5% [S1].' }))
    renderChat()

    await ask(user, 'When are proposals due?')
    await screen.findByText(/Proposals are due/)
    await ask(user, 'And the bond?')

    expect(askBid).toHaveBeenLastCalledWith(
      'bid-1',
      {
        question: 'And the bond?',
        history: [
          { role: 'user', content: 'When are proposals due?' },
          { role: 'assistant', content: 'Proposals are due October 30, 2026 at 2:00 PM [S1].' },
        ],
        compareBidIds: [],
      },
      expect.any(AbortSignal),
    )
    expect(await screen.findByText(/Bond is 5%/)).toBeInTheDocument()
  })

  it('marks answers the documents do not support', async () => {
    const user = userEvent.setup()
    askBid.mockResolvedValue(
      answer({ answer: 'The documents do not state a bond amount.', found: false, sources: [] }),
    )
    renderChat()

    await user.click(screen.getByRole('button', { name: 'What insurance or bonds are required?' }))

    expect(await screen.findByText('Not found in the documents')).toBeInTheDocument()
    expect(screen.getByText('The documents do not state a bond amount.')).toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'Answer sources' })).not.toBeInTheDocument()
  })

  it('shows an error and retries the same question once', async () => {
    const user = userEvent.setup()
    askBid
      .mockRejectedValueOnce(Object.assign(new Error('Document search is unavailable'), { status: 503 }))
      .mockResolvedValueOnce(answer())
    renderChat()

    await ask(user, 'When are proposals due?')
    expect(await screen.findByRole('alert')).toHaveTextContent('Document search is unavailable')

    await user.click(screen.getByRole('button', { name: /Retry/ }))

    expect(await screen.findByText(/Proposals are due/)).toBeInTheDocument()
    expect(askBid).toHaveBeenCalledTimes(2)
    expect(askBid.mock.calls[1][1]).toEqual({ question: 'When are proposals due?', history: [], compareBidIds: [] })
    expect(screen.getAllByText('When are proposals due?')).toHaveLength(1)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('disables input while pending and lets the user stop the request', async () => {
    const user = userEvent.setup()
    askBid.mockImplementation(
      (_bidId, _body, signal) =>
        new Promise((_resolve, reject) => {
          signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
        }),
    )
    renderChat()

    await ask(user, 'Who is the contact?')
    expect(screen.getByLabelText('Your question')).toBeDisabled()
    expect(screen.getByText(/Searching documents and drafting answer/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Stop/ }))

    expect(await screen.findByText('Stopped before an answer was returned.')).toBeInTheDocument()
    expect(screen.getByLabelText('Your question')).not.toBeDisabled()
  })

  it('clears the conversation', async () => {
    const user = userEvent.setup()
    askBid.mockResolvedValue(answer())
    renderChat()

    await ask(user, 'When are proposals due?')
    await screen.findByText(/Proposals are due/)
    await user.click(screen.getByRole('button', { name: /Clear chat/ }))

    expect(screen.queryByText(/Proposals are due/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'When are proposals due?' })).toBeInTheDocument()
  })

  it('compares with another indexed bid and labels sources by bid', async () => {
    const user = userEvent.setup()
    listBids.mockResolvedValue({
      items: [
        { id: 'bid-1', bid_identifier: 'bid1', title: 'Current', indexed_document_count: 2 },
        { id: 'bid-2', bid_identifier: 'bid2', title: 'Laptops', solicitation_number: 'E20', indexed_document_count: 3 },
        { id: 'bid-3', bid_identifier: 'bid3', title: 'Empty', indexed_document_count: 0 },
      ],
    })
    askBid.mockResolvedValue(
      answer({
        answer: 'Bid one is due Oct 30 [S1]; bid two on Nov 2 [S2].',
        sources: [
          { ...answer().sources[0], bid_id: 'bid-1', bid_label: 'Current' },
          { ...answer().sources[0], source_id: 'S2', chunk_id: 'chunk-2', bid_id: 'bid-2', bid_label: 'E20 - Laptops' },
        ],
      }),
    )
    renderChat()

    const select = await screen.findByLabelText('Compare with')
    expect(within(select).getAllByRole('option').map((option) => option.textContent)).toEqual([
      'No other bid',
      'Laptops (E20)',
    ])
    await user.selectOptions(select, 'bid-2')
    await ask(user, 'Compare the due dates')

    expect(askBid).toHaveBeenCalledWith(
      'bid-1',
      { question: 'Compare the due dates', history: [], compareBidIds: ['bid-2'] },
      expect.any(AbortSignal),
    )
    const sources = await screen.findByRole('list', { name: 'Answer sources' })
    expect(within(sources).getByText('Current')).toBeInTheDocument()
    expect(within(sources).getByText('E20 - Laptops')).toBeInTheDocument()
  })

  it('starts a new conversation when the compared bid changes', async () => {
    const user = userEvent.setup()
    listBids.mockResolvedValue({
      items: [{ id: 'bid-2', bid_identifier: 'bid2', title: 'Laptops', indexed_document_count: 1 }],
    })
    askBid.mockResolvedValue(answer())
    renderChat()

    await ask(user, 'When are proposals due?')
    await screen.findByText(/Proposals are due/)
    await user.selectOptions(await screen.findByLabelText('Compare with'), 'bid-2')

    expect(screen.queryByText(/Proposals are due/)).not.toBeInTheDocument()
  })

  it('shows stored records and policy notes without a document link', async () => {
    const user = userEvent.setup()
    askBid.mockResolvedValue(
      answer({
        answer: 'The due date needs review [R1]. Approval requires valid citations [P1].',
        sources: [
          {
            source_id: 'R1',
            kind: 'record',
            excerpt: 'Profile field Due Date: needs review.',
            record: 'Profile field Due Date: needs review.',
          },
          {
            source_id: 'P1',
            kind: 'policy',
            excerpt: 'Items with invalid citations cannot be approved.',
          },
        ],
      }),
    )
    renderChat()

    await ask(user, 'Can this be approved?')

    const sources = await screen.findByRole('list', { name: 'Answer sources' })
    expect(within(sources).getByText('Bid record')).toBeInTheDocument()
    expect(within(sources).getByText('System policy')).toBeInTheDocument()
    expect(within(sources).getByText('Stored analysis result (no source passage)')).toBeInTheDocument()
    expect(within(sources).queryByRole('link', { name: /Open/ })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'R1' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'P1' })).toBeInTheDocument()
  })

  it('truncates long earlier answers in history', async () => {
    const user = userEvent.setup()
    askBid.mockResolvedValueOnce(answer({ answer: 'x'.repeat(5000) })).mockResolvedValueOnce(answer())
    renderChat()

    await ask(user, 'Summarize')
    await screen.findByText('x'.repeat(5000))
    await ask(user, 'Shorter?')

    const { history } = askBid.mock.calls[1][1]
    expect(history[1].content).toHaveLength(4000)
  })

  it('asks the user to process documents when nothing is indexed', () => {
    renderChat({ indexedCount: 0 })

    expect(screen.getByText(/Upload and process documents before asking questions/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Upload documents' })).toHaveAttribute('href', '/bids/new?bid=bid-1')
    expect(screen.queryByLabelText('Your question')).not.toBeInTheDocument()
  })
})
