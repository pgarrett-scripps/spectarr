import { ArrowRight, FolderOpen, Grid3X3, List, Plus, Trash2, UsersRound } from 'lucide-react'
import { FormEvent, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { useResource } from '../api/useResource'
import { useAuth } from '../auth/AuthContext'
import { formatBytes, formatRelativeDate } from '../components/Data'
import { ApiErrorBanner, EmptyState, PageHeader } from '../components/Page'
import type { Project, UserRole } from '../types'

export function Library() {
  const resource = useResource(api.projects, [])
  const auth = useAuth()
  const [creating, setCreating] = useState(false)
  const [view, setView] = useState<'grid' | 'list'>('grid')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [membersProject, setMembersProject] = useState<Project | null>(null)

  const createProject = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    setSubmitting(true)
    setError(null)
    try {
      await api.createProject(String(form.get('name')), String(form.get('description') ?? ''))
      setCreating(false)
      resource.refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not create the project')
    } finally {
      setSubmitting(false)
    }
  }

  return <>
    <PageHeader title="Library" description="Organize acquisitions into projects, experiments, and samples." actions={<button className="button button-primary" onClick={() => {
      setError(null)
      setCreating(true)
    }}><Plus size={16} /> New project</button>} />
    {resource.error && <ApiErrorBanner message={resource.error} onRetry={resource.refresh} />}
    <div className="toolbar">
      <span className="toolbar-label">{resource.data.length} projects</span>
      <div className="view-toggle"><button className={view === 'grid' ? 'active' : ''} aria-label="Grid view" onClick={() => setView('grid')}><Grid3X3 size={16} /></button><button className={view === 'list' ? 'active' : ''} aria-label="List view" onClick={() => setView('list')}><List size={16} /></button></div>
    </div>
    {resource.data.length === 0 ? <EmptyState title="Your library is empty" description="Create a project, then import your first acquisition." action="Create project" onAction={() => setCreating(true)} /> : (
      <div className={`project-grid ${view === 'list' ? 'project-list' : ''}`}>
        {resource.data.map((project, index) => <article className="project-card" key={project.id}>
          <div className={`project-cover project-cover-${index % 4}`}><FolderOpen size={28} /><span>{project.runCount} runs</span></div>
          <div className="project-content">
            <h2>{project.name}</h2>
            <p>{project.description}</p>
            <div className="project-meta"><span>{formatBytes(project.sizeBytes)}</span><span>Updated {formatRelativeDate(project.updatedAt)}</span></div>
            <div className="project-links"><Link to={`/runs?project=${project.id}`} className="text-link">Open project <ArrowRight size={14} /></Link>{auth.user?.role === 'admin' && <button className="text-link" onClick={() => setMembersProject(project)}><UsersRound size={14} /> Members</button>}</div>
          </div>
        </article>)}
      </div>
    )}
    {creating && <div className="modal-backdrop" role="presentation">
      <section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="new-project-title">
        <div className="modal-header"><div><h2 id="new-project-title">New project</h2><p>Create a top-level home for related experiments and runs.</p></div><button className="icon-button" aria-label="Close" onClick={() => setCreating(false)}>×</button></div>
        <form onSubmit={event => void createProject(event)}>
          <div className="modal-fields">
            <label><span>Name</span><input name="name" required autoFocus maxLength={255} placeholder="Plasma DIA cohort" /></label>
            <label><span>Description</span><textarea name="description" rows={4} placeholder="Optional project notes" /></label>
          </div>
          {error && <div className="modal-error" role="alert">{error}</div>}
          <div className="modal-actions"><button type="button" className="button button-secondary" onClick={() => setCreating(false)}>Cancel</button><button type="submit" className="button button-primary" disabled={submitting}>{submitting ? 'Creating' : 'Create project'}</button></div>
        </form>
      </section>
    </div>}
    {membersProject && <ProjectMembers project={membersProject} onClose={() => setMembersProject(null)} />}
  </>
}

function ProjectMembers({ project, onClose }: { project: Project, onClose: () => void }) {
  const users = useResource(api.users, [])
  const memberships = useResource(() => api.projectMemberships(project.id), [])
  const [error, setError] = useState<string | null>(null)

  const add = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    setError(null)
    try {
      await api.addProjectMembership(project.id, String(form.get('userId')), String(form.get('role')) as UserRole)
      memberships.refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not add the project member')
    }
  }

  const remove = async (membershipId: string) => {
    setError(null)
    try {
      await api.deleteProjectMembership(project.id, membershipId)
      memberships.refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not remove the project member')
    }
  }

  return <div className="modal-backdrop" role="presentation"><section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="project-members-title">
    <div className="modal-header"><div><h2 id="project-members-title">{project.name} members</h2><p>Project roles can narrow an account's instance-wide access.</p></div><button className="icon-button" aria-label="Close" onClick={onClose}>×</button></div>
    {users.error && <ApiErrorBanner message={users.error} onRetry={users.refresh} />}
    {memberships.error && <ApiErrorBanner message={memberships.error} onRetry={memberships.refresh} />}
    <div className="membership-list">{memberships.data.map(membership => {
      const user = users.data.find(value => value.id === membership.userId)
      return <div key={membership.id}><span className="service-avatar">{(user?.displayName ?? 'U').slice(0, 2).toUpperCase()}</span><span><strong>{user?.displayName ?? membership.userId}</strong><small>{membership.role}</small></span><button className="icon-button" aria-label={`Remove ${user?.displayName ?? 'member'}`} onClick={() => void remove(membership.id)}><Trash2 size={14} /></button></div>
    })}</div>
    {users.data.length > 0 ? <form onSubmit={event => void add(event)}><div className="modal-fields membership-fields"><label><span>User</span><select name="userId" required>{users.data.map(user => <option value={user.id} key={user.id}>{user.displayName}</option>)}</select></label><label><span>Project role</span><select name="role" defaultValue="viewer"><option value="admin">Admin</option><option value="operator">Operator</option><option value="viewer">Viewer</option><option value="service">Service</option></select></label></div>{error && <div className="modal-error" role="alert">{error}</div>}<div className="modal-actions"><button type="button" className="button button-secondary" onClick={onClose}>Close</button><button type="submit" className="button button-primary"><Plus size={14} /> Add member</button></div></form> : <div className="modal-actions"><span className="muted-cell">No users are available to add.</span><button type="button" className="button button-secondary" onClick={onClose}>Close</button></div>}
  </section></div>
}
