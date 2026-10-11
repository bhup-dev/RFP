import { post } from './apiClient'

export function compareBids(
  bidIds,
  {
    dimensions = ['scope', 'products', 'commercial_terms', 'compliance', 'addenda'],
    extractionRunIds,
    allowIncompleteProfiles = false,
    signal,
  } = {},
) {
  const payload = {
    bid_ids: bidIds,
    dimensions,
    allow_incomplete_profiles: allowIncompleteProfiles,
  }
  if (extractionRunIds && Object.keys(extractionRunIds).length) {
    payload.extraction_run_ids = extractionRunIds
  }
  return post('/v1/bids/compare', payload, { signal })
}
