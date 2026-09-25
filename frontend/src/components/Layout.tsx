import { Activity, Braces, ChartNoAxesColumnIncreasing, ChevronDown, FolderOpen, HardDrive, Inbox, LayoutDashboard, List, LogOut, Menu, RadioTower, Search, Settings, Workflow, X } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { useDialogKeyboard } from './useDialogKeyboard'

const libraryNavigation = [
  { label: 'Overview', path: '/', icon: LayoutDashboard },
  { label: 'Projects', path: '/projects', icon: FolderOpen },
  { label: 'All runs', path: '/runs', icon: List },
  { label: 'Inbox', path: '/inbox', icon: Inbox }
]
const operationsNavigation = [
  { label: 'Processing', path: '/processing', icon: Workflow },
  { label: 'Activity', path: '/activity', icon: Activity },
  { label: 'Storage', path: '/storage', icon: HardDrive }
]
const configurationNavigation = [
  { label: 'Instrument agents', path: '/agents', icon: RadioTower, adminOnly: true },
  { label: 'Automation', path: '/automation', icon: Workflow, adminOnly: true },
  { label: 'API & MCP', path: '/integrations', icon: Braces },
  { label: 'Settings', path: '/settings', icon: Settings }
]

export function Layout({ children }: { children: ReactNode }) {
  useDialogKeyboard()
  const auth = useAuth()
  const [menuOpen, setMenuOpen] = useState(false)
  const [search, setSearch] = useState('')
  const location = useLocation()
  const navigate = useNavigate()
  const menuButton = useRef<HTMLButtonElement>(null)
  const sidebar = useRef<HTMLElement>(null)
  const configurationActive = configurationNavigation.some(item => location.pathname.startsWith(item.path))

  useEffect(() => setMenuOpen(false), [location.pathname])
  useEffect(() => {
    const desktop = window.matchMedia('(min-width: 801px)')
    const closeOnDesktop = () => {
      if (desktop.matches) setMenuOpen(false)
    }
    desktop.addEventListener('change', closeOnDesktop)
    return () => desktop.removeEventListener('change', closeOnDesktop)
  }, [])
  useEffect(() => {
    if (!menuOpen) return
    const controls = () => Array.from(sidebar.current?.querySelectorAll<HTMLElement>('a, button, summary') ?? [])
      .filter(element => element.getClientRects().length > 0)
      .filter(element => element.tagName === 'SUMMARY' || !element.closest('details:not([open])'))
    controls()[0]?.focus()
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMenuOpen(false)
        menuButton.current?.focus()
      }
      if (event.key === 'Tab') {
        const items = controls()
        const first = items[0]
        const last = items.at(-1)
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault()
          last?.focus()
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault()
          first?.focus()
        }
      }
    }
    document.addEventListener('keydown', keydown)
    return () => document.removeEventListener('keydown', keydown)
  }, [menuOpen])

  const links = (items: typeof libraryNavigation) => items.map(item => <NavLink key={item.path} to={item.path} end={item.path === '/' || item.path === '/runs'} className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
    <item.icon size={17} strokeWidth={1.6} /><span>{item.label}</span>
  </NavLink>)

  return <div className="shell">
    <a className="skip-link" href="#main-content">Skip to content</a>
    <aside ref={sidebar} className={`sidebar ${menuOpen ? 'sidebar-open' : ''}`} aria-label="Workspace navigation">
      <div className="brand">
        <ChartNoAxesColumnIncreasing size={25} strokeWidth={1.5} aria-hidden="true" />
        <div><strong>MassSpec</strong><span>Research data library</span></div>
        <button className="icon-button sidebar-close" onClick={() => { setMenuOpen(false)
          menuButton.current?.focus() }} aria-label="Close navigation"><X size={20} /></button>
      </div>
      <nav className="nav-list" aria-label="Main navigation">
        <span className="nav-heading">Library</span>
        {links(libraryNavigation)}
        <span className="nav-heading nav-heading-spaced">Operations</span>
        {links(operationsNavigation)}
        <details className="nav-configuration" open={configurationActive || undefined} key={configurationActive ? 'configuration-active' : 'configuration-inactive'}>
          <summary>Configuration <ChevronDown size={14} /></summary>
          {links(configurationNavigation.filter(item => !('adminOnly' in item) || auth.user?.role === 'admin'))}
        </details>
      </nav>
      <div className="sidebar-account">
        <div><strong>{auth.user?.displayName}</strong><span>{auth.mode === 'local' ? 'Local workspace' : auth.user?.role}</span></div>
        {auth.mode !== 'local' && <button className="icon-button" aria-label="Sign out" title="Sign out" onClick={() => void auth.logout()}><LogOut size={17} /></button>}
      </div>
    </aside>
    {menuOpen && <button className="sidebar-scrim" aria-label="Close navigation" onClick={() => setMenuOpen(false)} />}
    <div className="main-column">
      <header className="topbar">
        <button ref={menuButton} className="icon-button mobile-menu" onClick={() => setMenuOpen(true)} aria-label="Open navigation" aria-expanded={menuOpen}><Menu size={21} /></button>
        <form className="search-box" role="search" onSubmit={event => {
          event.preventDefault()
          if (search.trim()) navigate(`/runs?q=${encodeURIComponent(search.trim())}`)
        }}>
          <Search size={17} aria-hidden="true" />
          <input value={search} onChange={event => setSearch(event.target.value)} aria-label="Search all runs" placeholder="Search names, filenames, or checksums" />
          <button className="search-submit" aria-label="Search" type="submit">Search</button>
        </form>
        <span className="workspace-label">Mass spectrometry data</span>
      </header>
      <main id="main-content" className="main-content" tabIndex={-1}>{children}</main>
    </div>
  </div>
}
