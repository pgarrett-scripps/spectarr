import { expect, test } from '@playwright/test'

test('signs in and reaches live library data', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  await page.getByLabel('Username').fill(process.env.SPECTARR_E2E_USERNAME ?? 'release-admin')
  await page.getByLabel('Password').fill(process.env.SPECTARR_E2E_PASSWORD ?? 'release-rehearsal-admin-password')
  await page.getByRole('button', { name: 'Sign in' }).click()

  await expect(page.getByRole('heading', { name: 'Spectarr overview' })).toBeVisible()
  await expect(page.getByText(/smoke-/).first()).toBeVisible()
  await page.getByRole('link', { name: 'Runs', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Runs' })).toBeVisible()
  await expect(page.getByText(/smoke-/).first()).toBeVisible()
})
