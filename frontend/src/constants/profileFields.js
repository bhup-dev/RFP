export const PROFILE_FIELD_NAMES = Object.freeze([
  'Bid Number',
  'Title',
  'Due Date',
  'Bid Submission Type',
  'Term of Bid',
  'Pre Bid Meeting',
  'Installation',
  'Bid Bond Requirement',
  'Delivery Date',
  'Payment Terms',
  'Additional Documentation Required',
  'MFG for Registration',
  'Contract or Cooperative to use',
  'Model_no',
  'Part_no',
  'Product',
  'contact_info',
  'company_name',
  'Bid Summary',
  'Product Specification',
])

export const REVIEW_STATUSES = new Set([
  'needs_review',
  'conflicting',
  'unsupported',
  'partially_supported',
])

export function profileFieldAnchor(name) {
  return `field-${String(name).toLowerCase().replaceAll(/[^a-z0-9]+/g, '-')}`
}
