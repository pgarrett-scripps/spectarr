import {
  Activity,
  Braces,
  FlaskConical,
  Gauge,
  HardDrive,
  Inbox,
  Menu,
  LogOut,
  RadioTower,
  Search,
  Settings,
  ShieldOff,
  FolderKanban,
  Workflow,
  X
} from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

const navigation = [
  { label: 'Overview', path: '/', icon: Gauge },
  { label: 'Projects', path: '/projects', icon: FolderKanban },
  { label: 'Inbox', path: '/inbox', icon: Inbox },
  { label: 'Processing', path: '/processing', icon: FlaskConical },
  { label: 'Storage', path: '/storage', icon: HardDrive },
  { label: 'Activity', path: '/activity', icon: Activity },
  { label: 'Instrument agents', path: '/agents', icon: RadioTower, adminOnly: true },
  { label: 'Automation', path: '/automation', icon: Workflow, adminOnly: true }
]

const systemNavigation = [
  { label: 'API & MCP', path: '/integrations', icon: Braces },
  { label: 'Settings', path: '/settings', icon: Settings }
]

export function Layout({ children }: { children: ReactNode }) {
  const auth = useAuth()
  const [menuOpen, setMenuOpen] = useState(false)
  const [search, setSearch] = useState('')
  const location = useLocation()
  const navigate = useNavigate()

  useEffect(() => setMenuOpen(false), [location.pathname])

  return (
    <div className="shell">
      <aside className={`sidebar ${menuOpen ? 'sidebar-open' : ''}`}>
        <div className="brand">
          <div className="brand-mark"><FlaskConical size={22} /></div>
          <div>
            <strong>Spectarr</strong>
            <span>Mass spec workspace</span>
          </div>
          <button className="icon-button sidebar-close" onClick={() => setMenuOpen(false)} aria-label="Close navigation"><X size={20} /></button>
        </div>

        <nav className="nav-list" aria-label="Main navigation">
          {navigation.filter(item => !('adminOnly' in item) || auth.user?.role === 'admin').map(item => (
            <NavLink key={item.path} to={item.path} end={item.path === '/'} className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
              <item.icon size={19} />
              <span>{item.label}</span>
            </NavLink>
          ))}
          <span className="nav-heading nav-heading-spaced">System</span>
          {systemNavigation.map(item => (
            <NavLink key={item.path} to={item.path} className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
              <item.icon size={19} />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-status">
          <span className="status-dot status-good" />
          <div><strong>{auth.mode === 'local' ? 'Local mode' : 'Spectarr session'}</strong><span>{auth.mode === 'local' ? 'Login disabled' : 'Authenticated'}</span></div>
        </div>
      </aside>

      {menuOpen && <button className="sidebar-scrim" aria-label="Close navigation" onClick={() => setMenuOpen(false)} />}

      <div className="main-column">
        <header className="topbar">
          <button className="icon-button mobile-menu" onClick={() => setMenuOpen(true)} aria-label="Open navigation"><Menu size={21} /></button>
          <form className="search-box" onSubmit={event => {
            event.preventDefault()
            if (search.trim()) navigate(`/runs?q=${encodeURIComponent(search.trim())}`)
          }}>
            <input value={search} onChange={event => setSearch(event.target.value)} aria-label="Search all runs" placeholder="Search all runs..." />
            <button className="search-submit" aria-label="Search" type="submit"><Search size={17} /></button>
          </form>
          <div className="topbar-actions">
            <span className={`live-indicator ${auth.mode === 'local' ? 'local-mode-indicator' : ''}`}>{auth.mode === 'local' ? <ShieldOff size={13} /> : <i />}{auth.mode === 'local' ? 'No login' : 'Live'}</span>
            <span className="current-user"><strong>{auth.user?.displayName}</strong><small>{auth.user?.role}</small></span>
            {auth.mode !== 'local' && <button className="avatar" aria-label="Sign out" title="Sign out" onClick={() => void auth.logout()}><LogOut size={14} /></button>}
          </div>
        </header>
        <main className="main-content">{children}</main>
      </div>
    </div>
  )
}
