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
  await page.getByLabel('Sample').fill('Bundled MGF fixture')
  await page.getByLabel('Run name').fill(runName)
  await page.getByLabel('Source file').setInputFiles(fixture)
  await page.getByRole('button', { name: 'Import run' }).click()

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
