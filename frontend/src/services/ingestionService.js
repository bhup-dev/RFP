import { post } from './apiClient'

function ingestionPayload({ relativeDirectory, bidIdentifier }) {
  const payload = {}
  if (relativeDirectory?.trim()) payload.relative_directory = relativeDirectory.trim()
  if (bidIdentifier?.trim()) payload.bid_identifier = bidIdentifier.trim()
  return payload
}

export function scanDirectory(values, signal) {
  return post('/v1/ingestion/scan', ingestionPayload(values), { signal })
}

export function processDirectory(values, signal) {
  return post('/v1/ingestion/process', ingestionPayload(values), { signal })
}

export function processDocument(documentId, signal) {
  return post(`/v1/ingestion/documents/${encodeURIComponent(documentId)}/process`, undefined, {
    signal,
  })
}
