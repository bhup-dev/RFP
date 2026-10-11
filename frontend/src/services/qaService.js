import { post } from './apiClient'

export function askBid(bidId, { question, history = [], documentIds = [], compareBidIds = [] }, signal) {
  const body = { question, history }
  if (documentIds.length) body.document_ids = documentIds
  if (compareBidIds.length) body.compare_bid_ids = compareBidIds
  return post(`/v1/bids/${encodeURIComponent(bidId)}/ask`, body, { signal })
}
