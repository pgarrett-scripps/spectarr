import { useState } from 'react'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import type { SdrfDocument } from '../types'
import { SdrfEditor } from './SdrfEditor'

const fixture = (): SdrfDocument => ({
  id: 'sdrf', projectId: 'project', specificationVersion: '1.0', templates: [], revision: 1,
  status: 'draft', createdAt: '', updatedAt: '',
  columns: ['source name', 'characteristics[organism]', 'comment[data file]', 'comment[associated file]', 'comment[associated file]'],
  rows: Array.from({ length: 23 }, (_, index) => ({ id: `row-${index}`, position: index, runId: `run-${index}`, values: [`sample-${index}`, 'human', `run-${index}.raw`, 'first.txt', 'second.txt'] }))
})
afterEach(cleanup)

function setup(disabled = false) {
  const changed = vi.fn()
  function Harness() {
    const [document, setDocument] = useState(fixture)
    return <MemoryRouter><SdrfEditor document={document} disabled={disabled} editCell={(row, column, value) => {
      const next = { ...document, rows: document.rows.map((item, index) => index === row ? { ...item, values: item.values.map((cell, position) => position === column ? value : cell) } : item) }
      changed(next)
      setDocument(next)
    }} editColumn={vi.fn()} addColumn={vi.fn()} removeColumn={vi.fn()} addRow={() => setDocument({ ...document, rows: [...document.rows, { position: document.rows.length, values: document.columns.map(() => 'not available') }] })} removeRow={index => setDocument({ ...document, rows: document.rows.filter((_, position) => position !== index) })} /></MemoryRouter>
  }
  render(<Harness />)
  return changed
}

it('edits a duplicate column in a filtered row without changing mapping or the other duplicate', () => {
  const changed = setup()
  fireEvent.change(screen.getByLabelText('Find an entry'), { target: { value: 'run-22.raw' } })
  fireEvent.click(screen.getByRole('button', { name: /sample-22/ }))
  fireEvent.click(screen.getByRole('button', { name: /Acquisition/ }))
  fireEvent.change(screen.getByLabelText('Row 23, comment[associated file], column 5'), { target: { value: 'updated.txt' } })
  const document = changed.mock.calls[0][0] as SdrfDocument
  expect(document.rows[22]).toMatchObject({ runId: 'run-22', values: ['sample-22', 'human', 'run-22.raw', 'first.txt', 'updated.txt'] })
  expect(document.rows[0].values[4]).toBe('second.txt')
  expect(screen.getByRole('link', { name: 'View mapped run' })).toHaveAttribute('href', '/projects/project/runs/run-22')
})

it('allows viewers to page, select, and filter fields while edits stay disabled', () => {
  setup(true)
  fireEvent.click(screen.getByRole('button', { name: 'Next' }))
  fireEvent.click(screen.getByRole('button', { name: /sample-22/ }))
  fireEvent.change(screen.getByLabelText('Find a field'), { target: { value: 'associated' } })
  expect(screen.getByLabelText('Row 23, comment[associated file], column 5')).toBeDisabled()
  expect(screen.queryByLabelText('Row 23, source name, column 1')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Add row' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Remove SDRF row 23' })).toBeDisabled()
})

it('selects new rows and keeps an available selection after removing them', () => {
  setup()
  fireEvent.click(screen.getByRole('button', { name: 'Add row' }))
  expect(screen.getByLabelText('Row 24, source name, column 1')).toHaveValue('not available')
  fireEvent.click(screen.getByRole('button', { name: 'Remove SDRF row 24' }))
  expect(screen.getByLabelText('Row 23, source name, column 1')).toHaveValue('sample-22')
})

it('shows an empty search result without hiding or losing the selected draft', () => {
  setup()
  fireEvent.change(screen.getByLabelText('Find an entry'), { target: { value: 'missing-value' } })
  expect(within(screen.getByRole('region', { name: 'SDRF entries' })).queryByRole('button', { name: /sample/ })).not.toBeInTheDocument()
  expect(screen.getByText('No matching entries. Try another search.')).toBeVisible()
  expect(screen.getByLabelText('Row 1, source name, column 1')).toHaveValue('sample-0')
})
