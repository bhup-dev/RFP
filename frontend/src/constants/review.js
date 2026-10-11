export const REVIEW_STATUSES = Object.freeze([
  'pending',
  'approved',
  'rejected',
  'unresolved',
  'reevaluation_requested',
])

export const REVIEW_CATEGORIES = Object.freeze([
  'unsupported_claim',
  'invalid_citation',
  'conflicting_requirement',
  'ambiguous_deadline',
  'uncertain_product_specification',
  'inconsistent_agent_findings',
  'incomplete_profile',
  'comparison_uncertainty',
  'other',
])

export const REVIEW_SEVERITIES = Object.freeze(['low', 'medium', 'high', 'critical'])

export const REVIEW_DECISIONS = Object.freeze([
  ['approve', 'Approve finding'],
  ['reject', 'Reject finding'],
  ['request_re_evaluation', 'Request re-evaluation'],
  ['mark_unresolved', 'Mark unresolved'],
])
