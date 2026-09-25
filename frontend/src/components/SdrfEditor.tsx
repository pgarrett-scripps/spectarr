import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Plus, Trash2 } from 'lucide-react'
import type { SdrfDocument, SdrfValidationReport } from '../types'

interface Props {
  document: SdrfDocument
  validation?: SdrfValidationReport
  disabled: boolean
  editCell: (row: number, column: number, value: string) => void
  editColumn: (column: number, value: string) => void
  addColumn: () => void
  removeColumn: (column: number) => void
  addRow: () => void
  removeRow: (row: number) => void
}

const groups = ['Sample', 'Acquisition', 'Factors', 'Other fields']
const fieldGroup = (column: string) => {
  const name = column.toLowerCase()
  if (name === 'source name' || name.startsWith('characteristics[')) return 'Sample'
  if (name.startsWith('factor value[')) return 'Factors'
  if (name === 'assay name' || name === 'technology type' || name.startsWith('comment[')) return 'Acquisition'
  return 'Other fields'
}
const label = (column: string) => column.replace(/^(characteristics|comment|factor value)\[(.*)\]$/i, '$2')

export function SdrfEditor({ document, validation, disabled, editCell, editColumn, addColumn, removeColumn, addRow, removeRow }: Props) {
  const [selected, setSelected] = useState(0)
  const [query, setQuery] = useState('')
  const [fieldQuery, setFieldQuery] = useState('')
  const [page, setPage] = useState(0)
  const [group, setGroup] = useState('Sample')
  const rowIndex = Math.min(selected, document.rows.length - 1)
  const row = document.rows[rowIndex]
  const valueFor = (values: string[], name: string) => values[document.columns.findIndex(column => column.toLowerCase() === name)] || 'Not available'
  const filtered = document.rows.map((item, index) => ({ item, index })).filter(({ item }) => item.values.some(value => value.toLowerCase().includes(query.toLowerCase())))
  const lastPage = Math.max(0, Math.ceil(filtered.length / 20) - 1)
  const currentPage = Math.min(page, lastPage)
  const visible = filtered.slice(currentPage * 20, currentPage * 20 + 20)
  const fields = document.columns.map((column, index) => ({ column, index })).filter(({ column, index }) => `${column} ${row?.values[index] ?? ''}`.toLowerCase().includes(fieldQuery.toLowerCase()))

  const availableGroups = groups.filter(name => document.columns.some(column => fieldGroup(column) === name))
  const activeGroup = availableGroups.includes(group) ? group : availableGroups[0]

  return <div className="sdrf-workspace">
    <div className="sdrf-browser-toolbar">
      <label className="sdrf-search"><span>Find an entry</span><input type="search" placeholder="Search samples, files, or any value" value={query} onChange={event => { setQuery(event.target.value)
        setPage(0) }} /></label>
      <button className="button button-secondary button-small" disabled={disabled} onClick={() => { addRow()
        setSelected(document.rows.length)
        setQuery('')
        setPage(Math.floor(document.rows.length / 20))
        setFieldQuery('') }}><Plus size={14} /> Add row</button>
    </div>
    <div className="sdrf-entry-layout">
      <section className="sdrf-entry-list" aria-label="SDRF entries">
        <div className="sdrf-list-caption">{filtered.length} of {document.rows.length} entries</div>
        {visible.map(({ item, index }) => <button key={item.id ?? index} className={`sdrf-entry ${rowIndex === index ? 'sdrf-entry-selected' : ''}`} aria-pressed={rowIndex === index} onClick={() => { setSelected(index)
          setFieldQuery('') }}>
          <span className="sdrf-entry-heading"><strong>{valueFor(item.values, 'source name')}</strong><small>Row {index + 1}</small></span>
          <span>{valueFor(item.values, 'characteristics[organism]')}</span>
          <span className="sdrf-entry-file">{valueFor(item.values, 'comment[data file]')}</span>
          <small className={item.runId ? 'sdrf-mapped' : 'sdrf-unmapped'}>{item.runId ? 'Mapped to run' : 'Not mapped'}{validation?.messages.some(message => message.severity === 'error' && message.row === index) ? ' · Has errors' : ''}</small>
        </button>)}
        {!filtered.length && <p className="panel-placeholder">No matching entries. Try another search.</p>}
        {lastPage > 0 && <div className="sdrf-pagination"><button className="button button-secondary button-small" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Previous</button><span>{currentPage + 1} / {lastPage + 1}</span><button className="button button-secondary button-small" disabled={currentPage === lastPage} onClick={() => setPage(currentPage + 1)}>Next</button></div>}
      </section>
      <section className="sdrf-entry-detail" aria-label="Selected entry">
        {!row ? <p className="panel-placeholder">Add a row to start describing a sample.</p> : <>
          <div className="sdrf-detail-heading"><div><small>Row {rowIndex + 1}</small><h3>{valueFor(row.values, 'source name')}</h3>{row.runId && <Link to={`/projects/${document.projectId}/runs/${row.runId}`}>View mapped run</Link>}</div><button className="button button-secondary button-small" disabled={disabled} aria-label={`Remove SDRF row ${rowIndex + 1}`} onClick={() => { removeRow(rowIndex)
            setSelected(Math.max(0, rowIndex - 1)) }}><Trash2 size={14} /> Remove row</button></div>
          <label className="sdrf-search"><span>Find a field</span><input type="search" placeholder="Filter field names or values" value={fieldQuery} onChange={event => setFieldQuery(event.target.value)} /></label>
          {!fieldQuery && <nav className="sdrf-group-nav" aria-label="Field groups">{availableGroups.map(name => <button className="button button-small button-secondary" key={name} aria-pressed={activeGroup === name} onClick={() => setGroup(name)}>{name}<small>{document.columns.filter(column => fieldGroup(column) === name).length}</small></button>)}</nav>}
          <div className="sdrf-field-scroll">
            {(fieldQuery ? groups : [activeGroup]).map(group => {
              const members = fields.filter(({ column }) => fieldGroup(column) === group)
              return members.length > 0 && <section key={group} className="sdrf-field-group"><h4>{group}<small>{members.length} fields</small></h4><fieldset className="sdrf-fields" disabled={disabled}>
                {members.map(({ column, index }) => {
                  const messages = validation?.messages.filter(message => message.row === rowIndex && message.column === index) ?? []
                  const invalid = messages.some(message => message.severity === 'error')
                  return <label key={index}><span>{label(column)}</span><small>{column} · Column {index + 1}</small><input aria-label={`Row ${rowIndex + 1}, ${column}, column ${index + 1}`} aria-invalid={invalid} value={row.values[index] ?? ''} onChange={event => editCell(rowIndex, index, event.target.value)} />{messages.map((message, position) => <span className="sdrf-field-message" key={position}>{message.message}</span>)}</label>
                })}
              </fieldset></section>
            })}
            {!fields.length && <p className="panel-placeholder">No matching fields.</p>}
          </div>
        </>}
      </section>
    </div>
    <details className="metadata-disclosure sdrf-columns"><summary>Manage columns <span>{document.columns.length} fields, including repeated columns</span></summary><fieldset className="sdrf-column-list" disabled={disabled}>
      {document.columns.map((column, index) => <div key={index}><span>{index + 1}</span><input aria-label={`Column ${index + 1}`} value={column} onChange={event => editColumn(index, event.target.value)} /><button className="icon-button" aria-label={`Remove column ${index + 1}, ${column}`} onClick={() => removeColumn(index)}><Trash2 size={14} /></button></div>)}
      <button className="button button-secondary button-small" onClick={addColumn}><Plus size={14} /> Add SDRF column</button>
    </fieldset></details>
  </div>
}
