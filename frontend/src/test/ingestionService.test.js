import { afterEach, describe, expect, it, vi } from 'vitest'
import { processDirectory, processDocument, scanDirectory } from '../services/ingestionService'

afterEach(() => vi.unstubAllGlobals())

describe('ingestion service', () => {
  it('uses the server-directory scan contract', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ results: [] }), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await scanDirectory({ relativeDirectory: ' Bid1 ', bidIdentifier: ' Internal-1 ' })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/ingestion/scan',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          relative_directory: 'Bid1',
          bid_identifier: 'Internal-1',
        }),
      }),
    )
  })

  it('does not send browser files or empty optional fields', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ results: [] }), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await processDirectory({ relativeDirectory: '', bidIdentifier: '' })

    expect(fetchMock.mock.calls[0][1].body).toBe('{}')
    expect(fetchMock.mock.calls[0][1].headers['Content-Type']).toBe('application/json')
  })

  it('can resume a registered document through the verified endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ completed: true }), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await processDocument('document-id')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/ingestion/documents/document-id/process',
      expect.objectContaining({ method: 'POST' }),
    )
  })
})
