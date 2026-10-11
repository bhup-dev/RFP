import { FileCode2, FileText, FolderOpen } from 'lucide-react'
import { Link } from 'react-router-dom'
import { documentStatus } from '../../utils/documentStatus'
import EmptyState from '../common/EmptyState'
import StatusBadge from '../common/StatusBadge'

function filename(path = '') {
  return path.split('/').filter(Boolean).at(-1) || 'Unnamed document'
}

function fileType(document) {
  if (document.document_type) return document.document_type
  const extension = filename(document.relative_path).split('.').at(-1)
  return extension ? extension.toLowerCase() : 'unknown'
}

export default function DocumentList({ documents = [] }) {
  if (!documents.length) {
    return (
      <EmptyState
        title="No documents returned"
        description="The backend did not return documents for this operation."
      />
    )
  }

  return (
    <div className="document-list" aria-label="Documents">
      {documents.map((document) => {
        const type = fileType(document)
        const Icon = type === 'pdf' ? FileText : type === 'html' || type === 'htm' ? FileCode2 : FolderOpen
        return (
          <article
            className="document-row"
            key={document.document_id || `${document.relative_path}-${document.outcome || 'result'}`}
          >
            <div className="document-icon" aria-hidden="true">
              <Icon size={20} />
            </div>
            <div className="document-primary">
              <strong>{filename(document.relative_path)}</strong>
              <span>{document.relative_path}</span>
              {document.errors?.map((error) => (
                <span className="document-error" key={error}>{error}</span>
              ))}
            </div>
            <div className="document-meta">
              <span>{type.toUpperCase()}</span>
              {document.size_bytes != null && (
                <span>{Math.max(1, Math.round(document.size_bytes / 1024))} KB</span>
              )}
              {document.chunks_created > 0 && <span>{document.chunks_created} chunks</span>}
            </div>
            <StatusBadge status={documentStatus(document)} />
            {document.bid_id && (
              <Link className="document-link" to={`/bids/${document.bid_id}`}>
                Open bid
              </Link>
            )}
          </article>
        )
      })}
    </div>
  )
}
