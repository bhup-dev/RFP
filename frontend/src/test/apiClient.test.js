import { afterEach, describe, expect, it, vi } from 'vitest'
import { API_BASE_URL, ApiError, get, request } from '../services/apiClient'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('api client', () => {
  it('uses the configured API base URL and returns JSON', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: 'ok' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(get('/v1/example')).resolves.toEqual({ status: 'ok' })
    expect(API_BASE_URL).toBe('/api')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/example',
      expect.objectContaining({ method: 'GET' }),
    )
  })

  it('normalizes non-2xx responses', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: { code: 'not_ready', message: 'Bid not ready' } }), {
          status: 409,
        }),
      ),
    )

    await expect(get('/v1/example')).rejects.toMatchObject({
      name: 'ApiError',
      status: 409,
      code: 'not_ready',
      message: 'Bid not ready',
    })
  })

  it('normalizes network failures', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('connection refused')))
    await expect(get('/v1/example')).rejects.toMatchObject({
      code: 'network_error',
    })
  })

  it('returns null for an empty successful response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })))
    await expect(request('/v1/example')).resolves.toBeNull()
  })

  it('does not convert aborted requests into network errors', async () => {
    const aborted = new DOMException('Aborted', 'AbortError')
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(aborted))
    await expect(get('/v1/example')).rejects.toBe(aborted)
    expect(aborted).not.toBeInstanceOf(ApiError)
  })
})
