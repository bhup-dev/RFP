import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, post, uploadWithProgress } from '../services/apiClient'
import { documentFileUrl, uploadDocuments } from '../services/bidService'

class FakeXhr {
  static instances = []

  constructor() {
    this.headers = {}
    this.upload = {}
    this.status = 0
    this.responseText = ''
    FakeXhr.instances.push(this)
  }

  open(method, url) {
    this.method = method
    this.url = url
  }

  setRequestHeader(name, value) {
    this.headers[name] = value
  }

  send(body) {
    this.body = body
  }

  abort() {
    this.onabort?.()
  }

  respond(status, payload) {
    this.status = status
    this.responseText = JSON.stringify(payload)
    this.onload?.()
  }
}

afterEach(() => {
  FakeXhr.instances = []
  vi.unstubAllGlobals()
})

describe('multipart uploads', () => {
  it('sends FormData through fetch without forcing a JSON content type', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{"ok":true}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const form = new FormData()
    form.append('files', new Blob(['x']), 'a.pdf')

    await post('/v1/example', form)

    const [, options] = fetchMock.mock.calls[0]
    expect(options.body).toBe(form)
    expect(options.headers['Content-Type']).toBeUndefined()
  })

  it('reports progress and resolves with the parsed response', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXhr)
    const onProgress = vi.fn()
    const promise = uploadWithProgress('/v1/bids/b1/documents', new FormData(), { onProgress })
    const xhr = FakeXhr.instances[0]

    expect(xhr.method).toBe('POST')
    expect(xhr.url).toBe('/api/v1/bids/b1/documents')
    xhr.upload.onprogress({ lengthComputable: true, loaded: 25, total: 100 })
    expect(onProgress).toHaveBeenCalledWith(0.25)
    xhr.respond(200, { results: [] })

    await expect(promise).resolves.toEqual({ results: [] })
  })

  it('normalizes backend rejections into ApiError', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXhr)
    const promise = uploadWithProgress('/v1/bids/b1/documents', new FormData())
    FakeXhr.instances[0].respond(413, { detail: 'Too many files' })

    const error = await promise.catch((caught) => caught)
    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(413)
    expect(error.message).toBe('Too many files')
  })

  it('aborts the request when the signal fires', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXhr)
    const controller = new AbortController()
    const promise = uploadWithProgress('/v1/x', new FormData(), { signal: controller.signal })
    controller.abort()

    await expect(promise).rejects.toMatchObject({ name: 'AbortError' })
  })

  it('aligns files, roles, and addendum numbers by index', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXhr)
    const promise = uploadDocuments('bid 1', [
      { file: new File(['%PDF'], 'rfp.pdf'), role: 'original' },
      { file: new File(['%PDF'], 'add.pdf'), role: 'addendum', addendumNumber: '2' },
    ])
    const xhr = FakeXhr.instances[0]
    expect(xhr.url).toBe('/api/v1/bids/bid%201/documents')
    expect(xhr.body.getAll('files').map((file) => file.name)).toEqual(['rfp.pdf', 'add.pdf'])
    expect(xhr.body.getAll('roles')).toEqual(['original', 'addendum'])
    expect(xhr.body.getAll('addendum_numbers')).toEqual(['', '2'])
    xhr.respond(200, { results: [] })
    await promise
  })

  it('builds inline and download document URLs with optional page anchors', () => {
    expect(documentFileUrl('doc-1')).toBe('/api/v1/documents/doc-1/file')
    expect(documentFileUrl('doc-1', { page: 4 })).toBe('/api/v1/documents/doc-1/file#page=4')
    expect(documentFileUrl('doc-1', { download: true })).toBe('/api/v1/documents/doc-1/file?download=true')
  })
})
