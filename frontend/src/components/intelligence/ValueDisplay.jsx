function isMissingValue(value) {
  return value === null || value === undefined || value === ''
}

export default function ValueDisplay({ value, label = 'Value' }) {
  if (isMissingValue(value)) {
    return <span className="missing-value">Not found in source</span>
  }
  if (Array.isArray(value)) {
    if (!value.length) return <span className="missing-value">Not found in source</span>
    return (
      <ul className="structured-list" aria-label={label}>
        {value.map((item, index) => (
          <li key={`${index}-${String(item)}`}>
            <ValueDisplay value={item} label={`${label} item ${index + 1}`} />
          </li>
        ))}
      </ul>
    )
  }
  if (typeof value === 'object') {
    return (
      <dl className="structured-value">
        {Object.entries(value).map(([key, item]) => (
          <div key={key}>
            <dt>{key.replaceAll('_', ' ')}</dt>
            <dd><ValueDisplay value={item} label={key} /></dd>
          </div>
        ))}
      </dl>
    )
  }
  if (typeof value === 'boolean') return <span>{value ? 'Yes' : 'No'}</span>
  return <span className="preserve-text">{String(value)}</span>
}
