export function documentStatus(document) {
  if (document.errors?.length) return 'failed'
  if (document.completed) return 'completed'
  return document.outcome || document.indexing || document.chunking || document.parsing || 'unknown'
}
