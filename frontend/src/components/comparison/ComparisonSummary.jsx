import { AlertTriangle, CheckCircle2, GitCompareArrows } from 'lucide-react'
import { COMPARISON_CATEGORIES } from '../../constants/comparison'
import StatusBadge from '../common/StatusBadge'

function InsightList({ title, items }) {
  if (!items?.length) return null
  return (
    <section>
      <h3>{title}</h3>
      <ul>{items.map((item) => <li key={item}>{item}</li>)}</ul>
    </section>
  )
}

export default function ComparisonSummary({ report }) {
  return (
    <section className="panel comparison-summary">
      <div className="comparison-summary-heading">
        <div>
          <span className="eyebrow">Backend comparison report</span>
          <h2>Comparison summary</h2>
          <p>
            {report.bid_ids.length} bids · {report.summary.total_rows} structured comparison rows
          </p>
        </div>
        <div>
          <StatusBadge status={report.status} />
          {report.requires_human_review && (
            <span className="review-indicator"><AlertTriangle size={14} /> Human review required</span>
          )}
        </div>
      </div>
      <div className="comparison-metrics">
        {COMPARISON_CATEGORIES.map((category) => (
          <div key={category}>
            <StatusBadge status={category} />
            <strong>{report.summary[category] ?? 0}</strong>
          </div>
        ))}
      </div>
      <div className="comparison-insights">
        <InsightList title="Key similarities" items={report.key_similarities} />
        <InsightList title="Key differences" items={report.key_differences} />
        <InsightList title="Missing information" items={report.missing_information} />
        <InsightList title="Unresolved conflicts" items={report.unresolved_conflicts} />
      </div>
      {(report.warnings?.length > 0 || report.errors?.length > 0) && (
        <div className="comparison-report-messages">
          <AlertTriangle size={18} />
          <div>
            {[...(report.warnings || []), ...(report.errors || [])].map((message) => (
              <p key={message}>{message}</p>
            ))}
          </div>
        </div>
      )}
      {!report.requires_human_review && (
        <p className="comparison-complete-note">
          <CheckCircle2 size={16} /> No human-review requirement was reported.
        </p>
      )}
      <footer>
        <GitCompareArrows size={14} />
        Comparison ID <code>{report.comparison_id}</code> · Generated{' '}
        {new Date(report.created_at).toLocaleString()}
      </footer>
    </section>
  )
}
