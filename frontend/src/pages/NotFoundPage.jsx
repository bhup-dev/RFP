import { ArrowLeft } from 'lucide-react'
import { Link } from 'react-router-dom'
import PageContainer from '../components/layout/PageContainer'

export default function NotFoundPage() {
  return (
    <PageContainer title="Page not found" description="The requested workspace route does not exist.">
      <section className="panel not-found">
        <span className="not-found-code">404</span>
        <h2>Nothing to review here</h2>
        <p className="muted">Return to the dashboard to continue working with bid intelligence.</p>
        <Link className="button button-primary" to="/">
          <ArrowLeft size={17} /> Return to dashboard
        </Link>
      </section>
    </PageContainer>
  )
}
