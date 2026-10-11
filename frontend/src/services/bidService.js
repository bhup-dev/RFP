import { get, post, uploadWithProgress, API_BASE_URL } from './apiClient'

export const bidCapabilities = Object.freeze({
  list: true,
  detail: true,
  documents: true,
  intelligenceProfile: true,
})

export const JOB_ACTIVE_STATUSES = new Set(['queued', 'running'])

export function getIntelligenceProfile(bidId, { extractionRunId, signal } = {}) {
  const query = extractionRunId
    ? `?extraction_run_id=${encodeURIComponent(extractionRunId)}`
    : ''
  return get(`/v1/bids/${encodeURIComponent(bidId)}/intelligence-profile${query}`, {
    signal,
  })
}

export function createBid(values, signal) {
  const payload = { bid_identifier: values.bidIdentifier?.trim() ?? '' }
  for (const [key, field] of [
    ['title', 'title'],
    ['solicitationNumber', 'solicitation_number'],
    ['issuingOrganization', 'issuing_organization'],
  ]) {
    if (values[key]?.trim()) payload[field] = values[key].trim()
  }
  return post('/v1/bids', payload, { signal })
}

export function listBids({ limit = 20, offset = 0, search = '' } = {}, signal) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  if (search.trim()) params.set('search', search.trim())
  return get(`/v1/bids?${params}`, { signal })
}

export function getBid(bidId, signal) {
  return get(`/v1/bids/${encodeURIComponent(bidId)}`, { signal })
}

export function getUploadLimits(signal) {
  return get('/v1/uploads/limits', { signal })
}

export function uploadDocuments(bidId, entries, { onProgress, signal } = {}) {
  const form = new FormData()
  for (const entry of entries) {
    form.append('files', entry.file, entry.file.name)
    form.append('roles', entry.role || 'auto')
    form.append('addendum_numbers', entry.addendumNumber ? String(entry.addendumNumber) : '')
  }
  return uploadWithProgress(`/v1/bids/${encodeURIComponent(bidId)}/documents`, form, {
    onProgress,
    signal,
  })
}

export function startProcessing(bidId, jobType = 'ingest_and_analyze', signal) {
  return post(`/v1/bids/${encodeURIComponent(bidId)}/process`, { job_type: jobType }, { signal })
}

export function getJob(jobId, signal) {
  return get(`/v1/jobs/${encodeURIComponent(jobId)}`, { signal })
}

export function listBidJobs(bidId, signal) {
  return get(`/v1/bids/${encodeURIComponent(bidId)}/jobs`, { signal })
}

export function listRecentJobs(limit = 8, signal) {
  return get(`/v1/jobs?limit=${limit}`, { signal })
}

export function documentFileUrl(documentId, { download = false, page } = {}) {
  const query = download ? '?download=true' : ''
  const anchor = page ? `#page=${page}` : ''
  return `${API_BASE_URL}/v1/documents/${encodeURIComponent(documentId)}/file${query}${anchor}`
}
