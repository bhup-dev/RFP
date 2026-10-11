export const FALLBACK_UPLOAD_LIMITS = Object.freeze({
  max_file_bytes: 50 * 1024 * 1024,
  max_files_per_request: 20,
  allowed_extensions: ['.htm', '.html', '.pdf'],
  roles: ['auto', 'original', 'addendum', 'clarification', 'attachment'],
})

export const ROLE_LABELS = Object.freeze({
  auto: 'Detect from content',
  original: 'Original RFP',
  addendum: 'Addendum',
  clarification: 'Clarification / Q&A',
  attachment: 'Attachment',
})
