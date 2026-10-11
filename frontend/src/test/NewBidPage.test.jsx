import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import NewBidPage from '../pages/NewBidPage'
import {
  createBid,
  getJob,
  getUploadLimits,
  startProcessing,
  uploadDocuments,
} from '../services/bidService'

vi.mock('../services/bidService', async (importOriginal) => ({
  ...(await importOriginal()),
  createBid: vi.fn(),
  getBid: vi.fn(),
  getJob: vi.fn(),
  getUploadLimits: vi.fn(),
  startProcessing: vi.fn(),
  uploadDocuments: vi.fn(),
}))

const LIMITS = {
  max_file_bytes: 1024,
  max_files_per_request: 20,
  allowed_extensions: ['.htm', '.html', '.pdf'],
  roles: ['auto', 'original', 'addendum', 'clarification', 'attachment'],
}

const BID = { id: 'bid-1', bid_identifier: 'Docks-2026', title: null, document_count: 0 }

function renderPage(path = '/bids/new') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/bids/new" element={<NewBidPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

function pdf(name, size = 10) {
  return new File(['%PDF'.padEnd(size, '-')], name, { type: 'application/pdf' })
}

beforeEach(() => {
  vi.clearAllMocks()
  getUploadLimits.mockResolvedValue(LIMITS)
})

describe('new bid upload', () => {
  it('flags unsupported, empty, and oversized files before upload', async () => {
    const user = userEvent.setup({ applyAccept: false })
    renderPage()
    await screen.findByText(/up to 1.0 KB each/)

    await user.upload(screen.getByLabelText('Choose files'), [
      new File(['x'], 'notes.docx'),
      new File([], 'empty.pdf'),
      pdf('huge.pdf', 4096),
    ])

    const list = screen.getByRole('list', { name: 'Selected files' })
    expect(within(list).getByText(/Unsupported type/)).toBeInTheDocument()
    expect(within(list).getByText('File is empty')).toBeInTheDocument()
    expect(within(list).getByText(/Larger than the 1.0 KB limit/)).toBeInTheDocument()

    await user.type(screen.getByLabelText('Bid identifier *'), 'Docks-2026')
    await user.click(screen.getByRole('button', { name: /Create bid & upload/ }))
    expect(screen.getByRole('alert')).toHaveTextContent('Add at least one supported file')
    expect(createBid).not.toHaveBeenCalled()
  })

  it('creates the bid, uploads each file with its role, and reports mixed outcomes', async () => {
    const user = userEvent.setup()
    createBid.mockResolvedValue(BID)
    uploadDocuments
      .mockImplementationOnce(async (_bidId, _entries, { onProgress }) => {
        onProgress(0.5)
        return { results: [{ outcome: 'registered', original_filename: 'rfp.pdf' }] }
      })
      .mockRejectedValueOnce(Object.assign(new Error('File content does not match .pdf'), { status: 422 }))
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

    await user.type(screen.getByLabelText('Bid identifier *'), 'Docks-2026')
    await user.upload(screen.getByLabelText('Choose files'), [pdf('rfp.pdf'), pdf('fake.pdf')])
    await user.selectOptions(screen.getByLabelText('Role for rfp.pdf'), 'addendum')
    await user.type(screen.getByLabelText('Addendum number for rfp.pdf'), '2')
    await user.click(screen.getByRole('button', { name: /Create bid & upload/ }))

    expect(await screen.findByText(/1 file\(s\) stored, 0 already present/)).toBeInTheDocument()
    expect(createBid).toHaveBeenCalledWith(
      expect.objectContaining({ bidIdentifier: 'Docks-2026' }),
      expect.anything(),
    )
    expect(uploadDocuments).toHaveBeenCalledTimes(2)
    expect(uploadDocuments.mock.calls[0][1]).toEqual([
      expect.objectContaining({ role: 'addendum', addendumNumber: '2' }),
    ])
    expect(screen.getByText('File content does not match .pdf')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Start processing/ }))
    expect(startProcessing).toHaveBeenCalledWith('bid-1')
    expect(await screen.findByRole('region', { name: 'Processing status' })).toBeInTheDocument()
  })

  it('links to the existing bid when the identifier is taken', async () => {
    const user = userEvent.setup()
    createBid.mockRejectedValue(
      Object.assign(new Error('Bid identifier already exists'), {
        status: 409,
        details: { message: 'Bid identifier already exists', bid_id: 'bid-9' },
      }),
    )
    renderPage()

    await user.type(screen.getByLabelText('Bid identifier *'), 'Docks-2026')
    await user.upload(screen.getByLabelText('Choose files'), pdf('rfp.pdf'))
    await user.click(screen.getByRole('button', { name: /Create bid & upload/ }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Bid identifier already exists')
    expect(within(alert).getByRole('link', { name: 'add files to it' })).toHaveAttribute(
      'href',
      '/bids/new?bid=bid-9',
    )
    expect(uploadDocuments).not.toHaveBeenCalled()
  })

  it('shows the already-running job when processing is requested twice', async () => {
    const user = userEvent.setup()
    createBid.mockResolvedValue(BID)
    uploadDocuments.mockResolvedValue({ results: [{ outcome: 'duplicate', original_filename: 'rfp.pdf' }] })
    startProcessing.mockRejectedValue(
      Object.assign(new Error('A processing job is already active'), {
        status: 409,
        details: { job_id: 'job-7' },
      }),
    )
    getJob.mockResolvedValue({
      id: 'job-7',
      bid_id: 'bid-1',
      job_type: 'ingest_and_analyze',
      status: 'running',
      stage: 'analyzing',
      attempts: 1,
      max_attempts: 3,
    })
    renderPage()

    await user.type(screen.getByLabelText('Bid identifier *'), 'Docks-2026')
    await user.upload(screen.getByLabelText('Choose files'), pdf('rfp.pdf'))
    await user.click(screen.getByRole('button', { name: /Create bid & upload/ }))
    await user.click(await screen.findByRole('button', { name: /Start processing/ }))

    const status = await screen.findByRole('region', { name: 'Processing status' })
    expect(within(status).getByText('running')).toBeInTheDocument()
    expect(getJob).toHaveBeenCalledWith('job-7')
    expect(screen.queryByText('Unable to start processing')).not.toBeInTheDocument()
  })
})
