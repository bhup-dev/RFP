import { ExternalLink, FileText, MapPin } from 'lucide-react'
import { documentFileUrl } from '../../services/bidService'

export default function CitationList({ citations = [], role = 'supporting' }) {
  if (!citations.length) {
    return (
      <p className="citation-empty">
        {role === 'contradicting'
          ? 'No contradicting evidence was returned.'
          : 'No supporting evidence was returned for this field.'}
      </p>
    )
  }

  return (
    <div className={`citation-list citation-${role}`}>
      {citations.map((citation) => (
        <article className="citation-card" key={`${role}-${citation.chunk_id}`}>
          <div className="citation-heading">
            <FileText size={18} />
            <div>
              <strong>{citation.file_name || 'Source filename unavailable'}</strong>
              <span>
                <MapPin size={13} />
                {citation.page_number ? `Page ${citation.page_number}` : 'No page reference'}
                {citation.section_title ? ` · ${citation.section_title}` : ''}
              </span>
            </div>
            <span className="evidence-role">{role}</span>
          </div>
          <blockquote>{citation.excerpt || 'Source excerpt unavailable.'}</blockquote>
          <dl className="citation-identifiers">
            <div><dt>Bid</dt><dd><code>{citation.bid_id || 'Unavailable'}</code></dd></div>
            <div><dt>Document</dt><dd><code>{citation.document_id || 'Unavailable'}</code></dd></div>
            <div><dt>Chunk</dt><dd><code>{citation.chunk_id || 'Unavailable'}</code></dd></div>
            {citation.relevance_score != null && (
              <div>
                <dt>Retrieval relevance</dt>
                <dd>{Math.round(citation.relevance_score * 100)}%</dd>
              </div>
            )}
          </dl>
          {citation.document_id ? (
            <a
              className="citation-source-link"
              href={documentFileUrl(citation.document_id, { page: citation.page_number })}
              target="_blank"
              rel="noreferrer"
            >
              <ExternalLink size={13} /> Open source document
              {citation.page_number ? ` at page ${citation.page_number}` : ''}
            </a>
          ) : (
            <p className="citation-viewer-note">
              The source document cannot be opened because no document ID was returned.
            </p>
          )}
        </article>
      ))}
    </div>
  )
}
