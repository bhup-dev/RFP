export default function StatusBadge({ status = 'unknown' }) {
  const normalized = String(status).toLowerCase().replaceAll(' ', '_')
  return <span className={`status-badge status-${normalized}`}>{status.replaceAll('_', ' ')}</span>
}
