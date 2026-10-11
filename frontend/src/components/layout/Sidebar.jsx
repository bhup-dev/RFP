import { ClipboardCheck, FilePlus2, FileText, GitCompareArrows, LayoutDashboard, X } from 'lucide-react'
import { NavLink, useLocation } from 'react-router-dom'

const links = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/bids/new', label: 'New Bid', icon: FilePlus2, end: true },
  { to: '/bids', label: 'Bids', icon: FileText, exclude: '/bids/new' },
  { to: '/comparison', label: 'Bid Comparison', icon: GitCompareArrows },
  { to: '/reviews', label: 'Human Review', icon: ClipboardCheck },
]

export default function Sidebar({ open, onClose }) {
  const { pathname } = useLocation()
  return (
    <>
      {open && <button className="sidebar-backdrop" onClick={onClose} aria-label="Close navigation" />}
      <aside className={`sidebar ${open ? 'is-open' : ''}`} aria-label="Primary navigation">
        <div className="brand">
          <div className="brand-mark">R</div>
          <div>
            <strong>RFP Intelligence</strong>
            <span>Decision workspace</span>
          </div>
          <button className="icon-button sidebar-close" onClick={onClose} aria-label="Close navigation">
            <X size={20} />
          </button>
        </div>
        <nav className="sidebar-nav">
          <span className="nav-label">Workspace</span>
          {links.map(({ icon: Icon, exclude, ...link }) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.end}
              onClick={onClose}
              className={({ isActive }) =>
                `nav-link ${isActive && pathname !== exclude ? 'active' : ''}`
              }
            >
              <Icon size={19} />
              <span>{link.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-note">
          <span className="status-dot" />
          <div>
            <strong>Evidence first</strong>
            <span>All intelligence remains traceable to source chunks.</span>
          </div>
        </div>
      </aside>
    </>
  )
}
