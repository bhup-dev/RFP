export default function PageContainer({ title, description, actions, children }) {
  return (
    <section className="page-container">
      <div className="page-heading">
        <div>
          <h1>{title}</h1>
          {description && <p>{description}</p>}
        </div>
        {actions && <div className="page-actions">{actions}</div>}
      </div>
      {children}
    </section>
  )
}
