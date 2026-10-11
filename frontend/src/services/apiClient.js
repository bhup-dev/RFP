const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL || '/api'

export const API_BASE_URL = configuredBaseUrl.replace(/\/+$/, '')

export class ApiError extends Error {
  constructor(message, { status = 0, code = 'request_failed', details = null } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

function buildUrl(path, baseUrl = API_BASE_URL) {
  if (/^https?:\/\//i.test(path)) return path
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return `${baseUrl}${normalizedPath}`
}

function errorMessage(payload, status) {
  const detail = payload?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail.message === 'string') return detail.message
  if (typeof payload?.message === 'string') return payload.message
  return `Request failed with status ${status}`
}

export async function request(
  path,
  {
    method = 'GET',
    body,
    headers = {},
    signal,
    baseUrl = API_BASE_URL,
  } = {},
) {
  const requestHeaders = { Accept: 'application/json', ...headers }
  const options = { method, headers: requestHeaders, signal }

  if (body instanceof FormData) {
    options.body = body
  } else if (body !== undefined) {
    requestHeaders['Content-Type'] = 'application/json'
    options.body = JSON.stringify(body)
  }

  let response
  try {
    response = await fetch(buildUrl(path, baseUrl), options)
  } catch (error) {
    if (error?.name === 'AbortError') throw error
    throw new ApiError('Unable to reach the backend. Check the server and try again.', {
      code: 'network_error',
    })
  }

  return parseResponse(response.status, response.ok, await response.text())
}

function parseResponse(status, ok, raw) {
  let payload = null
  if (raw) {
    try {
      payload = JSON.parse(raw)
    } catch {
      if (!ok) {
        throw new ApiError(`Request failed with status ${status}`, {
          status,
          code: 'invalid_error_response',
        })
      }
      throw new ApiError('The backend returned an invalid response.', {
        status,
        code: 'invalid_json',
      })
    }
  }

  if (!ok) {
    throw new ApiError(errorMessage(payload, status), {
      status,
      code: payload?.detail?.code || payload?.code || 'request_failed',
      details: payload?.detail ?? null,
    })
  }

  return payload
}

// fetch cannot report upload progress, so multipart uploads use XMLHttpRequest.
export function uploadWithProgress(path, formData, { onProgress, signal, baseUrl = API_BASE_URL } = {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', buildUrl(path, baseUrl))
    xhr.setRequestHeader('Accept', 'application/json')

    const abort = () => xhr.abort()
    if (signal) {
      if (signal.aborted) {
        reject(new DOMException('The upload was aborted.', 'AbortError'))
        return
      }
      signal.addEventListener('abort', abort, { once: true })
    }
    const cleanup = () => signal?.removeEventListener('abort', abort)

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress?.(event.loaded / event.total)
    }
    xhr.onload = () => {
      cleanup()
      try {
        resolve(parseResponse(xhr.status, xhr.status >= 200 && xhr.status < 300, xhr.responseText))
      } catch (error) {
        reject(error)
      }
    }
    xhr.onerror = () => {
      cleanup()
      reject(new ApiError('Unable to reach the backend. Check the server and try again.', {
        code: 'network_error',
      }))
    }
    xhr.onabort = () => {
      cleanup()
      reject(new DOMException('The upload was aborted.', 'AbortError'))
    }
    xhr.send(formData)
  })
}

export function get(path, options) {
  return request(path, { ...options, method: 'GET' })
}

export function post(path, body, options) {
  return request(path, { ...options, method: 'POST', body })
}

export function getFromRoot(path, options) {
  return get(path, { ...options, baseUrl: '' })
}
