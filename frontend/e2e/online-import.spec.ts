import { expect, test } from '@playwright/test'

test('downloads a public PRIDE acquisition and reconnects after reload', async ({ page }, testInfo) => {
  test.skip(process.env.SPECTARR_E2E_PRIDE !== 'true', 'Enable explicitly to download a public PRIDE test acquisition')
  test.setTimeout(180_000)
  const suffix = crypto.randomUUID().slice(0, 8)
  const runName = `pride-browser-${suffix}`
  const filename = 'PRIDE_Exp_Complete_Ac_22134.pride.mgf.gz'
  await page.goto('/runs/import')
  if (await page.getByRole('heading', { name: 'Sign in', exact: true }).isVisible()) {
    await page.getByLabel('Username').fill(process.env.SPECTARR_E2E_USERNAME ?? 'release-admin')
    await page.getByLabel('Password').fill(process.env.SPECTARR_E2E_PASSWORD ?? 'release-rehearsal-admin-password')
    await page.getByRole('button', { name: 'Sign in' }).click()
    await page.goto('/runs/import')
  }
  await page.getByLabel('Source method').selectOption('online')
  await page.getByLabel('PRIDE accession or project URL').fill('PXD000001')
  await page.getByRole('button', { name: 'Look up dataset' }).click()
  await page.getByLabel('Search filenames').fill(filename)
  await page.getByLabel(`Select ${filename}`, { exact: true }).check({ timeout: 45_000 })
  await page.getByLabel('Project', { exact: true }).fill(`PRIDE browser ${suffix}`)
  await page.getByLabel('Experiment', { exact: true }).fill('Public repository import')
  await page.getByLabel(`Run name for ${filename}`, { exact: true }).fill(runName)
  await page.screenshot({ path: testInfo.outputPath('pride-selection.png'), fullPage: true })
  await page.getByRole('button', { name: 'Download 1 file' }).click()
  await expect(page.getByText(/1 download queued/)).toBeVisible({ timeout: 45_000 })
  await page.reload()
  await expect(page.getByLabel('Source method')).toHaveValue('online')
  await expect(page.getByRole('link', { name: `Open ${runName}`, exact: true })).toBeVisible({ timeout: 120_000 })
  await page.screenshot({ path: testInfo.outputPath('pride-complete.png'), fullPage: true })
  await page.getByRole('link', { name: `Open ${runName}`, exact: true }).click()
  await expect(page.getByRole('heading', { name: runName, exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Download source' })).toBeVisible()
})

test('imports PRIDE acquisitions with SDRF and optional full processing', async ({ page }, testInfo) => {
  test.skip(process.env.SPECTARR_E2E_PRIDE_SDRF !== 'true', 'Enable explicitly to download two public PRIDE acquisitions with SDRF')
  test.setTimeout(600_000)
  const fullPipeline = process.env.SPECTARR_E2E_PRIDE_PIPELINE === 'true'
  const suffix = crypto.randomUUID().slice(0, 8)
  const files = ['Adult_CD4Tcells_Gel_Velos_30_f42.raw', 'Adult_Monocytes_Gel_Velos_32_f30.raw']
  const msmsFile = 'Adult_Monocytes_bRP_Velos_31_f05.raw'
  if (fullPipeline) files.push(msmsFile)
  await page.goto('/settings?section=downloads')
  await page.getByLabel('Concurrent downloads').selectOption('3')
  await page.getByRole('button', { name: 'Save download settings' }).click()
  await expect(page.getByRole('status')).toContainText('Download settings saved')
  await page.reload()
  await expect(page.getByLabel('Concurrent downloads')).toHaveValue('3')
  await page.screenshot({ path: testInfo.outputPath('download-settings.png'), fullPage: true })
  await page.getByRole('button', { name: 'Use server default' }).click()
  await expect(page.getByRole('status')).toContainText('Download settings saved')
  await page.goto('/runs/import?source=online')
  await page.getByLabel('PRIDE accession or project URL').fill('PXD000561')
  await page.getByRole('button', { name: 'Look up dataset' }).click()
  await expect(page.getByLabel('Import SDRF metadata for selected acquisitions')).toBeChecked({ timeout: 90_000 })
  await page.getByLabel('Project', { exact: true }).fill(`PRIDE SDRF ${suffix}`)
  await page.getByLabel('Experiment', { exact: true }).fill('Parallel downloads')
  for (const [index, filename] of files.entries()) {
    await page.getByLabel('Search filenames').fill(filename)
    await page.getByRole('button', { name: 'Select matching files' }).click()
    await page.getByLabel(`Run name for ${filename}`, { exact: true }).fill(`sdrf-${suffix}-${index}`)
  }
  await expect(page.getByText(`${files.length} matching rows for the current selection.`, { exact: false })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('pride-search-and-selection.png'), fullPage: true })
  await page.getByRole('button', { name: `Download ${files.length} files` }).click()
  await expect(page.getByText(`${files.length} downloads queued.`, { exact: false })).toBeVisible({ timeout: 90_000 })
  await page.reload()
  for (const index of files.keys()) {
    const link = page.getByRole('link', { name: `Open sdrf-${suffix}-${index}`, exact: true })
    await expect(link).toBeVisible({ timeout: 240_000 })
    await expect(page.getByRole('row').filter({ has: link }).getByText('1 SDRF row imported', { exact: true })).toBeVisible()
  }
  await page.screenshot({ path: testInfo.outputPath('parallel-sdrf-import.png'), fullPage: true })
  const link = await page.getByRole('link', { name: `Open sdrf-${suffix}-0`, exact: true }).getAttribute('href')
  const projectId = link!.split('/')[2]
  const response = await page.request.get(`/api/v1/projects/${projectId}/sdrf`)
  expect(response.ok()).toBeTruthy()
  const document = await response.json() as { columns: string[], rows: { values: string[], run_id: string, sample_id: string }[] }
  expect(document.rows).toHaveLength(files.length)
  expect(document.rows.every(row => row.run_id && row.sample_id)).toBeTruthy()
  const fileColumn = document.columns.indexOf('comment[data file]')
  expect(document.rows.map(row => row.values[fileColumn]).sort()).toEqual([...files].sort())
  if (fullPipeline) {
    const imports = await (await page.request.get(`/api/v1/remote-imports?project_id=${projectId}`)).json() as { artifact_id: string, filename: string }[]
    for (const imported of imports) {
      let derivative = ''
      await expect.poll(async () => {
        const jobs = await (await page.request.get('/api/v1/jobs?limit=500')).json() as { kind: string, state: string, input_artifact_id: string, output_artifact_id: string }[]
        const conversion = jobs.find(job => job.kind === 'convert' && job.input_artifact_id === imported.artifact_id)
        derivative = conversion?.output_artifact_id ?? ''
        return conversion?.state
      }, { timeout: 180_000, intervals: [2000] }).toBe('succeeded')
      for (const artifact of [imported.artifact_id, derivative]) {
        await expect.poll(async () => (await (await page.request.get(`/api/v1/artifacts/${artifact}/spectrum-catalog`)).json()).status,
          { timeout: 120_000, intervals: [2000] }).toBe('ready')
      }
      const query = await page.request.post(`/api/v1/artifacts/${derivative}/spectra/query`, {
        data: { limit: 1, ...(imported.filename === msmsFile ? { ms_levels: [2] } : {}) }
      })
      expect(query.ok()).toBeTruthy()
      const spectra = await query.json() as { total: number, items: { id: string }[] }
      expect(spectra.total).toBeGreaterThan(0)
      const spectrum = await page.request.get(`/api/v1/artifacts/${derivative}/spectra/${spectra.items[0].id}`)
      expect(spectrum.ok()).toBeTruthy()
      const payload = await spectrum.json() as { arrays: { mz: number[], intensity: number[] } }
      expect(payload.arrays.mz.length).toBeGreaterThan(0)
      expect(payload.arrays.intensity).toHaveLength(payload.arrays.mz.length)
      await testInfo.attach(`${imported.filename}-spectrum.json`, { body: await spectrum.body(), contentType: 'application/json' })
    }
  }
})
