import { get, post } from './apiClient'

function reviewHeaders({ token, reviewerId }) {
  if (!token || !reviewerId) {
    throw new Error('Review requests require an authenticated reviewer session.')
  }
  return {
    Authorization: `Bearer ${token}`,
    'X-Reviewer-ID': reviewerId,
  }
}

export function listReviews(filters = {}, auth, signal) {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (value) query.set(key, value)
  }
  const suffix = query.size ? `?${query}` : ''
  return get(`/v1/reviews${suffix}`, {
    headers: reviewHeaders(auth),
    signal,
  })
}

export function getReview(reviewId, auth, signal) {
  return get(`/v1/reviews/${encodeURIComponent(reviewId)}`, {
    headers: reviewHeaders(auth),
    signal,
  })
}

export function submitReviewDecision(reviewId, decision, auth, signal) {
  return post(`/v1/reviews/${encodeURIComponent(reviewId)}/decision`, decision, {
    headers: reviewHeaders(auth),
    signal,
  })
}

export function getWorkflowReviewStatus(workflowId, auth, signal) {
  return get(`/v1/workflows/${encodeURIComponent(workflowId)}/review-status`, {
    headers: reviewHeaders(auth),
    signal,
  })
}
