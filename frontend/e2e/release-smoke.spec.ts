import { expect, test } from '@playwright/test'
import { resolve } from 'node:path'

test('creates a project and reaches indexed spectra through the live workflow', async ({ page }) => {
  test.setTimeout(120_000)
  const suffix = crypto.randomUUID().slice(0, 8)
  const projectName = `Browser release ${suffix}`
  const runName = `browser-run-${suffix}`
  const fixture = process.env.SPECTARR_E2E_FIXTURE ?? resolve('..', 'examples', 'demo.mgf')

  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  await page.getByLabel('Username').fill(process.env.SPECTARR_E2E_USERNAME ?? 'release-admin')
  await page.getByLabel('Password').fill(process.env.SPECTARR_E2E_PASSWORD ?? 'release-rehearsal-admin-password')
  await page.getByRole('button', { name: 'Sign in' }).click()

  await expect(page.getByRole('heading', { name: 'Spectarr overview' })).toBeVisible()
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Projects', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible()
  await page.getByRole('button', { name: 'New project' }).click()
  await page.getByLabel('Name').fill(projectName)
  await page.getByLabel('Description').fill('Browser release workflow verification')
  await page.getByRole('button', { name: 'Create project' }).click()

  await page.getByRole('link', { name: projectName, exact: true }).click()
  await expect(page.getByRole('heading', { name: projectName })).toBeVisible()
  await page.getByRole('link', { name: 'Import run' }).click()
  await expect(page.getByRole('heading', { name: 'Import mass spectrometry data' })).toBeVisible()
  await expect(page.getByLabel('Project')).toHaveValue(projectName)
  await expect(page.getByLabel('Project')).toHaveAttribute('readonly', '')
  await page.getByLabel('Experiment').fill('Release workflow')
  await page.getByLabel('Source files').setInputFiles(fixture)
  await page.getByLabel('Sample 1', { exact: true }).fill('Bundled MGF fixture')
  await page.getByLabel('Run name 1', { exact: true }).fill(runName)
  await page.getByRole('button', { name: 'Import 1 run' }).click()
  await expect(page.getByText('1 of 1 imported')).toBeVisible()
  await page.getByRole('link', { name: `Open ${runName}` }).click()

  await expect(page.getByRole('heading', { name: runName })).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Breadcrumb' })).toContainText(projectName)
  await expect(page.getByRole('button', { name: 'Download source' })).toBeVisible()
  await page.getByRole('link', { name: 'Spectra', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Spectrum viewer' })).toBeVisible()
  const catalogState = page.locator('.spectrum-catalog-state strong')
  await expect(catalogState).toHaveText(/Indexed catalog|Compatibility mode/, { timeout: 10_000 })
  if (await catalogState.textContent() === 'Compatibility mode') {
    await page.getByRole('button', { name: 'Build catalog' }).click()
  }
  await expect(catalogState).toHaveText('Indexed catalog', { timeout: 90_000 })
  await expect(page.getByText(/matching spectra/)).toBeVisible()
  await expect(page.getByRole('img', { name: 'centroid spectrum' })).toBeVisible()
})


test('imports a batch and retries a lost upload response without duplicate sources', async ({ page }, testInfo) => {
  const projectName = `Batch browser ${crypto.randomUUID().slice(0, 8)}`
  await page.goto('/')
  await page.getByLabel('Username').fill(process.env.SPECTARR_E2E_USERNAME ?? 'release-admin')
  await page.getByLabel('Password').fill(process.env.SPECTARR_E2E_PASSWORD ?? 'release-rehearsal-admin-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Spectarr overview' })).toBeVisible()
  await page.goto('/runs/import')
  await page.getByLabel('Project', { exact: true }).fill(projectName)
  await page.getByLabel('Experiment', { exact: true }).fill('Batch experiment')
  const content = Buffer.from('BEGIN IONS\nTITLE=batch\nPEPMASS=445.34\n100 20\n200 30\nEND IONS\n')
  await page.getByLabel('Source files').setInputFiles([
    { name: 'batch-one.mgf', mimeType: 'application/octet-stream', buffer: content },
    { name: 'batch-two.mgf', mimeType: 'application/octet-stream', buffer: content }
  ])
  await expect(page.getByLabel('Run name 1', { exact: true })).toHaveValue('batch-one')
  await page.screenshot({ path: testInfo.outputPath('batch-import-preview.png'), fullPage: true })
  let lostResponse = false
  await page.route('**/api/v1/runs/*/artifacts/upload', async route => {
    if (lostResponse) return route.continue()
    lostResponse = true
    const response = await route.fetch()
    expect(response.status()).toBe(201)
    await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Simulated lost response' }) })
  })
  await page.getByRole('button', { name: 'Import 2 runs' }).click()
  await expect(page.getByText('1 of 2 imported, 1 failed')).toBeVisible()
  await page.getByRole('button', { name: 'Retry batch-one', exact: true }).click()
  await expect(page.getByText('2 of 2 imported')).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('batch-import-complete.png'), fullPage: true })
  await page.getByRole('link', { name: 'View project runs' }).click()
  await expect(page.getByRole('heading', { name: projectName })).toBeVisible()
  await expect(page.getByText('Showing 1 to 2 of 2 runs')).toBeVisible()
  await page.getByRole('link', { name: /batch-one/ }).first().click()
  await expect(page.getByRole('heading', { name: 'batch-one', exact: true })).toBeVisible()
  const token = await page.evaluate(() => sessionStorage.getItem('spectarr_access_token'))
  const runId = page.url().split('/runs/')[1].split('/')[0]
  const artifacts = await page.request.get(`/api/v1/artifacts?run_id=${runId}`, { headers: { Authorization: `Bearer ${token}` } })
  expect(artifacts.ok()).toBeTruthy()
  const rows = await artifacts.json() as { role: string }[]
  expect(rows.filter(item => item.role === 'source')).toHaveLength(1)
})
