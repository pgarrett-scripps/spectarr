import { Check, DatabaseBackup, Clipboard, KeyRound, LockKeyhole, Plus, Shield, Trash2, UserRound } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import { useResource } from '../api/useResource'
import { useAuth } from '../auth/AuthContext'
import { formatRelativeDate } from '../components/Data'
import { ApiErrorBanner, PageHeader, Panel } from '../components/Page'
import type { UserRole } from '../types'
import { BackupSettings } from '../components/BackupSettings'

const sections = [
  { id: 'security', label: 'Security', icon: Shield },
  { id: 'users', label: 'Users', icon: UserRound, adminOnly: true },
  { id: 'backups', label: 'Backups', icon: DatabaseBackup, adminOnly: true }
]

export function SettingsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const section = searchParams.get('section') ?? 'security'
  const setSection = (value: string) => setSearchParams({ section: value }, { replace: true })
  const auth = useAuth()
  const visibleSections = sections.filter(item => !('adminOnly' in item) || auth.user?.role === 'admin')
  return <>
    <PageHeader title="Settings" description="Manage credentials, users, and backups." actions={<Link className="button button-secondary" to="/automation">Processing automation</Link>} />
    <div className="settings-layout">
      <nav className="settings-nav">{visibleSections.map(item => <button className={section === item.id ? 'active' : ''} onClick={() => setSection(item.id)} key={item.id}><item.icon size={17} />{item.label}</button>)}</nav>
      <div className="settings-content">
        {section === 'security' && <SecuritySettings />}
        {section === 'users' && <UsersSettings />}
        {section === 'backups' && auth.user?.role === 'admin' && <BackupSettings />}
      </div>
    </div>
  </>
}

function UsersSettings() {
  const auth = useAuth()
  const resource = useResource(api.users, [])
  const [creating, setCreating] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    setSubmitting(true)
    setError(null)
    try {
      await api.createUser({
        username: String(form.get('username')),
        displayName: String(form.get('displayName')),
        password: String(form.get('password')),
        role: String(form.get('role')) as UserRole
      })
      setCreating(false)
      resource.refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not create the user')
    } finally {
      setSubmitting(false)
    }
  }

  return <>
    {resource.error && <ApiErrorBanner message={resource.error} onRetry={resource.refresh} />}
    <Panel title="Users" subtitle="Local accounts and instance-wide roles" actions={<button className="button button-primary button-small" onClick={() => setCreating(true)}><Plus size={14} /> Add user</button>}>
      <div className="user-list">{resource.data.map(user => {
        const protectedLocalUser = auth.mode === 'local' && user.username === auth.localUser
        return <div className="user-row" key={user.id}>
        <span className="service-avatar">{user.displayName.slice(0, 2).toUpperCase()}</span>
        <div><strong>{user.displayName}</strong><small>{user.username} · {user.lastLoginAt ? `last signed in ${formatRelativeDate(user.lastLoginAt)}` : 'never signed in'}</small></div>
        {protectedLocalUser ? <><span className="format-chip">Admin</span><span className="muted-cell">Required by local mode</span></> : <><select aria-label={`Role for ${user.username}`} value={user.role} onChange={event => void api.updateUser(user.id, { role: event.target.value as UserRole }).then(resource.refresh).catch(reason => setError(reason instanceof Error ? reason.message : 'Could not update role'))}><option value="admin">Admin</option><option value="operator">Operator</option><option value="viewer">Viewer</option><option value="service">Service</option></select><button className="button button-ghost button-small" onClick={() => void api.updateUser(user.id, { active: !user.active }).then(resource.refresh).catch(reason => setError(reason instanceof Error ? reason.message : 'Could not update user'))}>{user.active ? 'Disable' : 'Enable'}</button></>}
      </div>})}</div>
      {error && <div className="modal-error setting-error" role="alert">{error}</div>}
    </Panel>
    {creating && <div className="modal-backdrop" role="presentation"><section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="new-user-title">
      <div className="modal-header"><div><h2 id="new-user-title">New user</h2><p>Create a local account and assign its initial role.</p></div><button className="icon-button" aria-label="Close" onClick={() => setCreating(false)}>×</button></div>
      <form onSubmit={event => void create(event)}><div className="modal-fields">
        <label><span>Username</span><input name="username" required autoFocus /></label>
        <label><span>Display name</span><input name="displayName" required /></label>
        <label><span>Password</span><input name="password" type="password" required minLength={12} /></label>
        <label><span>Role</span><select name="role" defaultValue="viewer"><option value="admin">Admin</option><option value="operator">Operator</option><option value="viewer">Viewer</option><option value="service">Service</option></select></label>
      </div>{error && <div className="modal-error" role="alert">{error}</div>}<div className="modal-actions"><button type="button" className="button button-secondary" onClick={() => setCreating(false)}>Cancel</button><button type="submit" className="button button-primary" disabled={submitting}>{submitting ? 'Creating' : 'Create user'}</button></div></form>
    </section></div>}
  </>
}

function SecuritySettings() {
  const auth = useAuth()
  const resource = useResource(api.tokens, [])
  const [creating, setCreating] = useState(false)
  const [secret, setSecret] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [passwordMessage, setPasswordMessage] = useState<string | null>(null)
  const [passwordError, setPasswordError] = useState<string | null>(null)

  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    setError(null)
    try {
      const token = await api.createToken(String(form.get('name')), form.getAll('scopes').map(String))
      setSecret(token.token ?? null)
      resource.refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not create the token')
    }
  }

  return <>
    {auth.mode === 'local' && <div className={`local-auth-notice ${auth.allowRemoteNoAuth ? 'local-auth-warning' : ''}`}><Shield size={17} /><div><strong>Authentication is disabled</strong><span>Dashboard requests use the local administrator automatically. Agent, worker, and bearer credentials remain supported and keep their own identities.{auth.allowRemoteNoAuth ? ' Remote no-login access is explicitly enabled.' : ''}</span></div></div>}
    {resource.error && <ApiErrorBanner message={resource.error} onRetry={resource.refresh} />}
    <Panel title="API tokens" subtitle="Scoped credentials for scripts, MCP, workers, and Searcharr" actions={<button className="button button-primary button-small" onClick={() => {
      setSecret(null)
      setCopied(false)
      setError(null)
      setCreating(true)
    }}><KeyRound size={14} /> Create token</button>}>
      <div className="user-list">{resource.data.map(token => <div className="user-row token-row" key={token.id}>
        <span className="service-avatar service-avatar-muted"><KeyRound size={14} /></span><div><strong>{token.name}</strong><small>{token.scopes.join(', ')} · created {formatRelativeDate(token.createdAt)}</small></div><span>{token.lastUsedAt ? `Used ${formatRelativeDate(token.lastUsedAt)}` : 'Never used'}</span><button className="icon-button" aria-label={`Revoke ${token.name}`} onClick={() => void api.deleteToken(token.id).then(resource.refresh).catch(reason => setError(reason instanceof Error ? reason.message : 'Could not revoke token'))}><Trash2 size={15} /></button>
      </div>)}</div>
      {error && <div className="modal-error setting-error" role="alert">{error}</div>}
    </Panel>
    {auth.mode === 'password' && <Panel title="Change password" subtitle="Changing your password revokes your other signed-in sessions">
      <form className="password-form" onSubmit={event => {
        event.preventDefault()
        const target = event.currentTarget
        const form = new FormData(target)
        const currentPassword = String(form.get('currentPassword'))
        const newPassword = String(form.get('newPassword'))
        const confirmation = String(form.get('confirmation'))
        setPasswordError(null)
        setPasswordMessage(null)
        if (newPassword !== confirmation) {
          setPasswordError('New password confirmation does not match')
          return
        }
        void api.changePassword(currentPassword, newPassword).then(() => {
          target.reset()
          setPasswordMessage('Password changed. Other sessions were revoked.')
        }).catch(reason => setPasswordError(reason instanceof Error ? reason.message : 'Could not change the password'))
      }}>
        <label><span>Current password</span><input name="currentPassword" type="password" required autoComplete="current-password" /></label>
        <label><span>New password</span><input name="newPassword" type="password" required minLength={12} autoComplete="new-password" /></label>
        <label><span>Confirm new password</span><input name="confirmation" type="password" required minLength={12} autoComplete="new-password" /></label>
        <button className="button button-secondary" type="submit"><LockKeyhole size={14} /> Change password</button>
        {passwordMessage && <div className="setting-success"><Check size={14} />{passwordMessage}</div>}
        {passwordError && <div className="modal-error setting-error" role="alert">{passwordError}</div>}
      </form>
    </Panel>}
    {creating && <div className="modal-backdrop" role="presentation"><section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="new-token-title">
      <div className="modal-header"><div><h2 id="new-token-title">Create API token</h2><p>The secret is shown only once.</p></div><button className="icon-button" aria-label="Close" onClick={() => setCreating(false)}>×</button></div>
      {secret ? <div className="token-result"><strong>Token secret</strong><p>Store this credential securely. Spectarr cannot display it again.</p><div className="endpoint"><code>{secret}</code><button className="icon-button" aria-label="Copy token" onClick={() => {
        setError(null)
        void navigator.clipboard.writeText(secret).then(() => setCopied(true)).catch(reason => setError(reason instanceof Error ? reason.message : 'Could not copy to the clipboard'))
      }}>{copied ? <Check size={15} /> : <Clipboard size={15} />}</button></div>{error && <div className="modal-error" role="alert">{error}</div>}<button className="button button-primary" onClick={() => setCreating(false)}>Done</button></div> : <form onSubmit={event => void create(event)}><div className="modal-fields">
        <label><span>Name</span><input name="name" required autoFocus placeholder="Searcharr indexer" /></label>
        <label className="check-field"><input type="checkbox" name="scopes" value="library:read" defaultChecked /><span>Read library</span></label>
        <label className="check-field"><input type="checkbox" name="scopes" value="library:write" /><span>Write library</span></label>
        <label className="check-field"><input type="checkbox" name="scopes" value="jobs:write" /><span>Manage jobs</span></label>
        <label className="check-field"><input type="checkbox" name="scopes" value="agents:write" /><span>Upload from agents</span></label>
      </div>{error && <div className="modal-error" role="alert">{error}</div>}<div className="modal-actions"><button type="button" className="button button-secondary" onClick={() => setCreating(false)}>Cancel</button><button type="submit" className="button button-primary">Create token</button></div></form>}
    </section></div>}
  </>
}
