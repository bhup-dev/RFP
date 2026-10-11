import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import DocumentList from '../components/documents/DocumentList'

describe('document list', () => {
  it('renders backend document fields and safe errors', () => {
    render(
      <MemoryRouter>
        <DocumentList
          documents={[
            {
              document_id: 'doc-1',
              bid_id: 'bid-1',
              relative_path: 'Bid1/Addendum 1.pdf',
              document_type: 'pdf',
              outcome: 'failed',
              size_bytes: 2048,
              errors: ['Document could not be parsed'],
            },
          ]}
        />
      </MemoryRouter>,
    )

    expect(screen.getByText('Addendum 1.pdf')).toBeInTheDocument()
    expect(screen.getByText('2 KB')).toBeInTheDocument()
    expect(screen.getByText('Document could not be parsed')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open bid' })).toHaveAttribute('href', '/bids/bid-1')
  })

  it('handles an empty document response', () => {
    render(
      <MemoryRouter>
        <DocumentList documents={[]} />
      </MemoryRouter>,
    )
    expect(screen.getByText('No documents returned')).toBeInTheDocument()
  })
})
