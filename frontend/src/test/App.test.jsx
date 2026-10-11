import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../app/App'

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation(async (url) => {
      const path = String(url)
      let body = { status: 'ready', postgresql: 'ready', qdrant: 'ready' }
      if (path.startsWith('/api/v1/bids')) body = { items: [], total: 0, limit: 20, offset: 0 }
      if (path.startsWith('/api/v1/jobs')) body = []
      return new Response(JSON.stringify(body), { status: 200 })
    }),
  )
})

function renderAt(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

describe('application routing', () => {
  it('renders the application without crashing', async () => {
    renderAt()
    expect(screen.getByRole('heading', { name: 'Dashboard', level: 1 })).toBeInTheDocument()
    expect(await screen.findByText('Connected services')).toBeInTheDocument()
  })

  it('navigates between supported routes', async () => {
    const user = userEvent.setup()
    renderAt()
    await user.click(screen.getByRole('link', { name: 'Bids' }))
    expect(screen.getByRole('heading', { name: 'Bids', level: 1 })).toBeInTheDocument()
    expect(await screen.findByText('No bids available')).toBeInTheDocument()
  })

  it('renders the not-found page for unknown routes', () => {
    renderAt('/does-not-exist')
    expect(screen.getByRole('heading', { name: 'Page not found', level: 1 })).toBeInTheDocument()
  })

  it('opens mobile navigation through an accessible control', async () => {
    const user = userEvent.setup()
    renderAt('/reviews')
    const navigation = screen.getByLabelText('Primary navigation')
    expect(navigation).not.toHaveClass('is-open')
    await user.click(screen.getByRole('button', { name: 'Open navigation' }))
    expect(navigation).toHaveClass('is-open')
    await user.keyboard('{Escape}')
    expect(navigation).not.toHaveClass('is-open')
  })

  it('keeps missing review authentication honest', () => {
    renderAt('/reviews')
    expect(screen.getByRole('heading', { name: 'Start reviewer session' })).toBeInTheDocument()
    expect(screen.getByText(/shared token and trusts the supplied reviewer ID/i)).toBeInTheDocument()
    expect(screen.queryByText(/saved successfully/i)).not.toBeInTheDocument()
  })
})
