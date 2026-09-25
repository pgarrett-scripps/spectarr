import { Plus, Search, Trash2, UsersRound } from 'lucide-react'
import { FormEvent, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { useResource } from '../api/useResource'
import { useAuth } from '../auth/AuthContext'
import { formatBytes, formatRelativeDate } from '../components/Data'
import { ApiErrorBanner, EmptyState, LoadingState, PageHeader } from '../components/Page'
import type { Project, UserRole } from '../types'

export function Projects() {
  const resource = useResource(api.projects, [], undefined, 'projects')
  const auth = useAuth()
  const canCreate = auth.user?.role === 'admin' || auth.user?.role === 'operator'
  const [creating, setCreating] = useState(false)
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState('updated')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [membersProject, setMembersProject] = useState<Project | null>(null)

  const projects = resource.data.filter(project => !project.systemKey)
  const visibleProjects = projects.filter(project => `${project.name} ${project.description ?? ''}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
    .sort((left, right) => sort === 'name' ? left.name.localeCompare(right.name) : sort === 'runs' ? right.runCount - left.runCount : Date.parse(right.updatedAt) - Date.parse(left.updatedAt))

  const createProject = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (submitting) return
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
    <PageHeader title="Projects" description="Choose a project to browse its runs, experiments, and scientific metadata." actions={canCreate ? <button className="button button-primary" onClick={() => {
      setError(null)
      setCreating(true)
    }}><Plus size={16} /> New project</button> : undefined} />
    {resource.error && <ApiErrorBanner message={resource.error} onRetry={resource.refresh} />}
    <div className="project-tools">
      <label className="table-search"><Search size={16} aria-hidden="true" /><input value={query} onChange={event => setQuery(event.target.value)} aria-label="Search projects" placeholder="Find a project" /></label>
      <label className="sort-control"><span>Sort by</span><select value={sort} onChange={event => setSort(event.target.value)} aria-label="Sort projects"><option value="updated">Recently updated</option><option value="name">Project name</option><option value="runs">Run count</option></select></label>
      <span className="result-count" role="status">{visibleProjects.length} {visibleProjects.length === 1 ? 'project' : 'projects'}</span>
    </div>
    {resource.loading && resource.data.length === 0 ? <LoadingState label="Loading projects" /> : resource.error && resource.data.length === 0 ? null : visibleProjects.length === 0 ? <EmptyState title={query ? 'No matching projects' : 'No projects yet'} description={query ? 'Try a project name or a word from its description.' : canCreate ? 'Create a project, then import your first acquisition.' : 'Research projects shared with you will appear here.'} action={query ? 'Clear search' : 'Create project'} onAction={query ? () => setQuery('') : canCreate ? () => setCreating(true) : undefined} /> : (
      <section className="panel project-table-panel"><div className="table-scroll" role="region" aria-label="Project table" tabIndex={0}><table className="project-table">
        <caption className="sr-only">Research projects</caption>
        <thead><tr><th scope="col">Project</th><th scope="col" className="numeric-cell">Runs</th><th scope="col" className="numeric-cell">Data</th><th scope="col">Sample metadata</th><th scope="col">Updated</th>{auth.user?.role === 'admin' && <th scope="col"><span className="sr-only">Members</span></th>}</tr></thead>
        <tbody>{visibleProjects.map(project => <tr key={project.id}>
          <td><Link to={`/projects/${project.id}/runs`} className="project-name">{project.name}</Link><p className="project-description">{project.description || 'No description'}</p></td>
          <td className="numeric-cell">{project.runCount.toLocaleString()}</td>
          <td className="numeric-cell">{formatBytes(project.sizeBytes)}</td>
          <td><span className={`metadata-status metadata-status-${project.sdrf?.status ?? 'none'}`}>{project.sdrf ? `${project.sdrf.status === 'valid' ? 'Validated' : project.sdrf.status === 'invalid' ? 'Needs review' : 'Draft'} SDRF` : 'Not recorded'}</span></td>
          <td className="muted-cell"><time dateTime={project.updatedAt} title={new Date(project.updatedAt).toLocaleString()}>{formatRelativeDate(project.updatedAt)}</time></td>
          {auth.user?.role === 'admin' && <td><button className="icon-button" aria-label={`Manage members of ${project.name}`} onClick={() => setMembersProject(project)}><UsersRound size={17} /></button></td>}
        </tr>)}</tbody>
      </table></div></section>
    )}
    {creating && <div className="modal-backdrop" role="presentation">
      <section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="new-project-title">
        <div className="modal-header"><div><h2 id="new-project-title">New project</h2><p>Create a top-level home for related experiments and runs.</p></div><button className="icon-button" aria-label="Close" disabled={submitting} onClick={() => setCreating(false)}>×</button></div>
        <form onSubmit={event => void createProject(event)}>
          <div className="modal-fields">
            <label><span>Name</span><input name="name" required autoFocus maxLength={255} placeholder="Plasma DIA cohort" /></label>
            <label><span>Description</span><textarea name="description" rows={4} placeholder="Optional project notes" /></label>
          </div>
          {error && <div className="modal-error" role="alert">{error}</div>}
          <div className="modal-actions"><button type="button" className="button button-secondary" disabled={submitting} onClick={() => setCreating(false)}>Cancel</button><button type="submit" className="button button-primary" disabled={submitting}>{submitting ? 'Creating' : 'Create project'}</button></div>
        </form>
      </section>
    </div>}
    {membersProject && <ProjectMembers project={membersProject} onClose={() => setMembersProject(null)} />}
  </>
}

function ProjectMembers({ project, onClose }: { project: Project, onClose: () => void }) {
  const [submitting, setSubmitting] = useState(false)
  const users = useResource(api.users, [])
  const memberships = useResource(() => api.projectMemberships(project.id), [])
  const [error, setError] = useState<string | null>(null)

  const add = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (submitting) return
    const form = new FormData(event.currentTarget)
    setError(null)
    setSubmitting(true)
    try {
      await api.addProjectMembership(project.id, String(form.get('userId')), String(form.get('role')) as UserRole)
      memberships.refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not add the project member')
    } finally {
      setSubmitting(false)
    }
  }

  const remove = async (membershipId: string) => {
    if (submitting) return
    setSubmitting(true)
    setError(null)
    try {
      await api.deleteProjectMembership(project.id, membershipId)
      memberships.refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not remove the project member')
    } finally {
      setSubmitting(false)
    }
  }

  return <div className="modal-backdrop" role="presentation"><section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="project-members-title">
    <div className="modal-header"><div><h2 id="project-members-title">{project.name} members</h2><p>Project roles can narrow an account's instance-wide access.</p></div><button className="icon-button" aria-label="Close" disabled={submitting} onClick={onClose}>×</button></div>
    {users.error && <ApiErrorBanner message={users.error} onRetry={users.refresh} />}
    {memberships.error && <ApiErrorBanner message={memberships.error} onRetry={memberships.refresh} />}
    <div className="membership-list">{memberships.data.map(membership => {
      const user = users.data.find(value => value.id === membership.userId)
      return <div key={membership.id}><span className="service-avatar">{(user?.displayName ?? 'U').slice(0, 2).toUpperCase()}</span><span><strong>{user?.displayName ?? membership.userId}</strong><small>{membership.role}</small></span><button className="icon-button" aria-label={`Remove ${user?.displayName ?? 'member'}`} disabled={submitting} onClick={() => void remove(membership.id)}><Trash2 size={14} /></button></div>
    })}</div>
    {users.data.length > 0 ? <form onSubmit={event => void add(event)}><div className="modal-fields membership-fields"><label><span>User</span><select name="userId" required>{users.data.map(user => <option value={user.id} key={user.id}>{user.displayName}</option>)}</select></label><label><span>Project role</span><select name="role" defaultValue="viewer"><option value="admin">Admin</option><option value="operator">Operator</option><option value="viewer">Viewer</option><option value="service">Service</option></select></label></div>{error && <div className="modal-error" role="alert">{error}</div>}<div className="modal-actions"><button type="button" className="button button-secondary" disabled={submitting} onClick={onClose}>Close</button><button type="submit" className="button button-primary" disabled={submitting}><Plus size={14} /> Add member</button></div></form> : <div className="modal-actions"><span className="muted-cell">No users are available to add.</span><button type="button" className="button button-secondary" disabled={submitting} onClick={onClose}>Close</button></div>}
  </section></div>
}
