import { FlaskConical, Pencil, Plus, RotateCw, Sparkles, ToggleLeft, ToggleRight, Workflow } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { api } from '../api/client'
import { useResource } from '../api/useResource'
import { ApiErrorBanner, PageHeader, Panel } from '../components/Page'
import type { ConversionFormat, ProcessingProfile } from '../types'

export function Automation() {
  const rules = useResource(api.automationRules, [])
  const profiles = useResource(api.processingProfiles, [])
  const projects = useResource(api.projects, [])
  const instruments = useResource(api.instruments, [])
  const [creating, setCreating] = useState(false)
  const [scope, setScope] = useState<'global' | 'project' | 'instrument'>('global')
  const [extractMetadata, setExtractMetadata] = useState(true)
  const [selectedProfiles, setSelectedProfiles] = useState<Set<string>>(new Set())
  const [editingProfile, setEditingProfile] = useState<ProcessingProfile | 'new' | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (selectedProfiles.size || !profiles.data.length) return
    const standard = profiles.data.find(profile => profile.name === 'Standard mzML')
    if (standard) setSelectedProfiles(new Set([standard.id]))
  }, [profiles.data, selectedProfiles.size])

  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    if (!extractMetadata && selectedProfiles.size === 0) {
      setError('Choose metadata extraction or at least one processing profile')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await api.createAutomationRule({
        name: String(form.get('name')),
        enabled: true,
        scope,
        projectId: scope === 'project' ? String(form.get('projectId')) : undefined,
        instrumentId: scope === 'instrument' ? String(form.get('instrumentId')) : undefined,
        extractMetadata,
        profileIds: [...selectedProfiles],
        deepQc: extractMetadata && form.get('deepQc') === 'on'
      })
      setCreating(false)
      rules.refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not create the rule')
    } finally {
      setSubmitting(false)
    }
  }

  const toggleRule = async (id: string, enabled: boolean) => {
    setError(null)
    try {
      await api.updateAutomationRule(id, { enabled })
      rules.refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not update the rule')
    }
  }
  const toggleProfileEnabled = async (profile: ProcessingProfile) => {
    setError(null)
    try {
      await api.updateProcessingProfile(profile.id, { enabled: !profile.enabled })
      profiles.refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not update the profile')
    }
  }

  return <>
    <PageHeader title="Automation" description="Define desired derivatives and metadata for new and existing source files." actions={<><button className="button button-secondary" onClick={() => {
      rules.refresh()
      profiles.refresh()
    }}><RotateCw size={15} /> Refresh</button><button className="button button-primary" onClick={() => {
      setError(null)
      setCreating(true)
    }}><Plus size={16} /> New rule</button></>} />
    {rules.error && <ApiErrorBanner message={rules.error} onRetry={rules.refresh} />}
    {profiles.error && <ApiErrorBanner message={`Profiles: ${profiles.error}`} onRetry={profiles.refresh} />}
    {error && <div className="message-banner" role="alert">{error}</div>}
    <Panel title="Desired-output rules" subtitle="Rules reconcile after upload, Inbox assignment, project moves, and policy changes">
      {rules.data.length === 0 ? <div className="settings-placeholder">No automation rules are configured. Source files can still be processed in batches.</div> : <div className="rule-list">{rules.data.map(rule => <div className="rule-row" key={rule.id}>
        <span className="rule-icon"><Workflow size={18} /></span>
        <div><strong>{rule.name}</strong><small>{rule.scope} scope</small><span className="rule-actions">{rule.extractMetadata && <i><Sparkles size={12} />Metadata</i>}{rule.profileIds.map(id => <i key={id}><FlaskConical size={12} />{profiles.data.find(profile => profile.id === id)?.name ?? 'Conversion profile'}</i>)}{rule.profileIds.length === 0 && rule.generateMzml && <i><FlaskConical size={12} />Legacy conversion</i>}{rule.deepQc && <i><Sparkles size={12} />Deep QC</i>}</span></div>
        <button className="icon-button" aria-label={`${rule.enabled ? 'Disable' : 'Enable'} ${rule.name}`} onClick={() => void toggleRule(rule.id, !rule.enabled)}>{rule.enabled ? <ToggleRight size={24} /> : <ToggleLeft size={24} />}</button>
      </div>)}</div>}
    </Panel>
    <Panel title="Processing profiles" subtitle="Named, revisioned MSConvert configurations" actions={<button className="button button-secondary button-small" onClick={() => setEditingProfile('new')}><Plus size={14} /> New profile</button>}>
      <div className="profile-list">{profiles.data.map(profile => <article className="profile-row" key={profile.id}><span className="format-chip">{profile.outputFormat}</span><div><strong>{profile.name}</strong><small>Revision {profile.revision}{profile.system ? ' · built in' : ' · custom'}</small><p>{profile.description}</p></div><button className="button button-ghost button-small" onClick={() => setEditingProfile(profile)}><Pencil size={14} /> Edit</button><button className="icon-button" aria-label={`${profile.enabled ? 'Disable' : 'Enable'} ${profile.name}`} onClick={() => void toggleProfileEnabled(profile)}>{profile.enabled ? <ToggleRight size={24} /> : <ToggleLeft size={24} />}</button></article>)}</div>
    </Panel>
    {creating && <div className="modal-backdrop" role="presentation"><section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="new-rule-title">
      <div className="modal-header"><div><h2 id="new-rule-title">New automation rule</h2><p>The same policy applies to future sources and existing matching runs.</p></div><button className="icon-button" aria-label="Close" onClick={() => setCreating(false)}>×</button></div>
      <form onSubmit={event => void create(event)}><div className="modal-fields">
        <label><span>Name</span><input name="name" required autoFocus placeholder="Prepare files for search" /></label>
        <label><span>Scope</span><select name="scope" value={scope} onChange={event => setScope(event.target.value as typeof scope)}><option value="global">All sources</option><option value="project">Project</option><option value="instrument">Instrument</option></select></label>
        {scope === 'project' && <label><span>Project</span><select name="projectId" required>{projects.data.filter(project => !project.systemKey).map(project => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>}
        {scope === 'instrument' && <label><span>Instrument</span><select name="instrumentId" required>{instruments.data.map(instrument => <option key={instrument.id} value={instrument.id}>{instrument.name}</option>)}</select></label>}
        <label className="check-field"><input type="checkbox" checked={extractMetadata} onChange={event => setExtractMetadata(event.target.checked)} /><span>Extract scientific metadata</span></label>
        {extractMetadata && <label className="check-field"><input type="checkbox" name="deepQc" defaultChecked /><span>Calculate deep QC and previews</span></label>}
        <fieldset className="choice-fieldset"><legend>Generate derivatives</legend>{profiles.data.filter(profile => profile.enabled).map(profile => <label className="profile-choice" key={profile.id}><input type="checkbox" checked={selectedProfiles.has(profile.id)} onChange={() => setSelectedProfiles(current => toggleSet(current, profile.id))} /><span><strong>{profile.name}</strong><small>{profile.outputFormat} · revision {profile.revision}</small></span></label>)}</fieldset>
      </div>{error && <div className="modal-error" role="alert">{error}</div>}<div className="modal-actions"><button type="button" className="button button-secondary" onClick={() => setCreating(false)}>Cancel</button><button type="submit" className="button button-primary" disabled={submitting}>{submitting ? 'Creating' : 'Create and reconcile'}</button></div></form>
    </section></div>}
    {editingProfile && <ProfileEditor profile={editingProfile === 'new' ? undefined : editingProfile} onClose={() => setEditingProfile(null)} onSaved={() => {
      setEditingProfile(null)
      profiles.refresh()
    }} />}
  </>
}

function ProfileEditor({ profile, onClose, onSaved }: { profile?: ProcessingProfile, onClose: () => void, onSaved: () => void }) {
  const [format, setFormat] = useState<ConversionFormat>(profile?.outputFormat ?? 'mzML')
  const [preset, setPreset] = useState<ProcessingProfile['parameters']['preset']>(profile?.parameters.preset)
  const [centroid, setCentroid] = useState(profile?.parameters.filters.some(filter => filter.kind === 'peak_picking') ?? false)
  const [ms2Only, setMs2Only] = useState(profile?.parameters.filters.some(filter => filter.kind === 'ms_level') ?? false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const parameters: ProcessingProfile['parameters'] = {
      preset,
      filters: preset ? [] : [
        ...(centroid ? [{ kind: 'peak_picking', algorithm: 'vendor', ms_levels: [1, 2] }] : []),
        ...(ms2Only ? [{ kind: 'ms_level', levels: [2] }] : [])
      ],
      mzPrecision: preset ? 64 : Number(form.get('mzPrecision')) as 32 | 64,
      intensityPrecision: preset ? 32 : Number(form.get('intensityPrecision')) as 32 | 64,
      compression: preset ? 'none' : String(form.get('compression')) as ProcessingProfile['parameters']['compression'],
      indexed: preset ? true : form.get('indexed') === 'on'
    }
    setSubmitting(true)
    setError(null)
    try {
      const values = { name: profile?.system ? profile.name : String(form.get('name')), description: String(form.get('description')), outputFormat: format, parameters }
      if (profile) await api.updateProcessingProfile(profile.id, values)
      else await api.createProcessingProfile(values)
      onSaved()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not save the profile')
    } finally {
      setSubmitting(false)
    }
  }
  return <div className="modal-backdrop" role="presentation"><section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="profile-editor-title"><div className="modal-header"><div><h2 id="profile-editor-title">{profile ? `Edit ${profile.name}` : 'New processing profile'}</h2><p>Changing conversion settings creates a new profile revision.</p></div><button className="icon-button" aria-label="Close" onClick={onClose}>×</button></div><form onSubmit={event => void save(event)}><div className="modal-fields">
    <label><span>Name</span><input name="name" required defaultValue={profile?.name} disabled={profile?.system} placeholder="DIA search mzML" /></label>
    <label><span>Description</span><textarea name="description" rows={3} defaultValue={profile?.description} /></label>
    <label><span>MSCLI named config</span><select value={preset ?? ''} onChange={event => {
      const value = event.target.value as ProcessingProfile['parameters']['preset'] | ''
      setPreset(value || undefined)
      if (value) setFormat(value === 'casanovo_mgf' ? 'MGF' : 'mzML')
    }}><option value="">Custom typed settings</option><option value="sage">Sage</option><option value="biosaur">Biosaur</option><option value="blitzff">BlitzFF</option><option value="casanovo">Casanovo mzML</option><option value="casanovo_mgf">Casanovo MGF</option></select></label>
    <label><span>Output format</span><select value={format} onChange={event => setFormat(event.target.value as ConversionFormat)}><option value="mzML">mzML</option><option value="MGF">MGF</option><option value="MS2">MS2</option><option value="mzXML">mzXML</option></select></label>
    {preset ? <div className="scope-summary"><strong>Settings managed by MSCLI</strong><span>Spectarr will execute the packaged named config and record its name with the profile revision.</span></div> : <><div className="field-grid"><label><span>m/z precision</span><select name="mzPrecision" defaultValue={profile?.parameters.mzPrecision ?? 64}><option value="64">64 bit</option><option value="32">32 bit</option></select></label><label><span>Intensity precision</span><select name="intensityPrecision" defaultValue={profile?.parameters.intensityPrecision ?? 32}><option value="32">32 bit</option><option value="64">64 bit</option></select></label></div>
    <label><span>Compression</span><select name="compression" defaultValue={profile?.parameters.compression ?? 'zlib'}><option value="zlib">zlib</option><option value="numpress">Numpress</option><option value="none">None</option></select></label>
    <label className="check-field"><input type="checkbox" name="indexed" defaultChecked={profile?.parameters.indexed ?? true} /><span>Write an indexed output</span></label>
    <label className="check-field"><input type="checkbox" checked={centroid} onChange={event => setCentroid(event.target.checked)} /><span>Apply vendor peak picking</span></label>
    <label className="check-field"><input type="checkbox" checked={ms2Only} onChange={event => setMs2Only(event.target.checked)} /><span>Keep MS2 spectra only</span></label></>}
  </div>{error && <div className="modal-error" role="alert">{error}</div>}<div className="modal-actions"><button type="button" className="button button-secondary" onClick={onClose}>Cancel</button><button type="submit" className="button button-primary" disabled={submitting}>{submitting ? 'Saving' : 'Save profile'}</button></div></form></section></div>
}

function toggleSet(current: Set<string>, id: string) {
  const next = new Set(current)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  return next
}
