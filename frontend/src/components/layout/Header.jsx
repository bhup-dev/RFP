import { Menu } from 'lucide-react'

export default function Header({ onMenu }) {
  return (
    <header className="app-header">
      <button className="icon-button mobile-menu" onClick={onMenu} aria-label="Open navigation">
        <Menu size={21} />
      </button>
      <div>
        <span className="header-eyebrow">Enterprise AI workspace</span>
        <strong className="header-title">RFP Intelligence Platform</strong>
      </div>
      <div className="environment-pill" aria-label="Environment">
        Local
      </div>
    </header>
  )
}
