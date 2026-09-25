import { expect, test, type Page } from '@playwright/test'

const projects = [
  { id: 'plasma', name: 'Plasma proteomics', description: 'Longitudinal treatment cohort', run_count: 8, size_bytes: 2048, updated_at: '2026-09-22T12:00:00Z' },
  { id: 'yeast', name: 'Yeast reference', description: 'Instrument performance', run_count: 3, size_bytes: 1024, updated_at: '2026-09-24T12:00:00Z' }
]

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/v1/auth/config') return route.fulfill({ json: { mode: 'local' } })
    if (path === '/api/v1/auth/me') return route.fulfill({ json: { id: 'admin', username: 'researcher', display_name: 'Researcher', role: 'admin', active: true } })
    if (path === '/api/v1/projects') return route.fulfill({ json: projects })
    return route.fulfill({ status: 404, json: { detail: 'Unexpected UI fixture request' } })
  })
})

async function openProjects(page: Page) {
  await page.goto('/projects')
  await expect(page.getByRole('table', { name: 'Research projects' })).toBeVisible()
}

for (const width of [320, 390, 768, 1024, 1440]) {
  test(`project table stays inside the page at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await openProjects(page)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
    const region = page.getByRole('region', { name: 'Project table' })
    await region.focus()
    await expect(region).toBeFocused()
    if (width < 800) {
      await page.keyboard.press('ArrowRight')
      await expect.poll(() => region.evaluate(element => element.scrollLeft)).toBeGreaterThan(0)
    }
    await page.getByRole('textbox', { name: 'Search projects' }).fill('instrument')
    await expect(page.getByRole('link', { name: 'Yeast reference', exact: true })).toBeVisible()
    await expect(page.getByRole('link', { name: 'Plasma proteomics', exact: true })).toHaveCount(0)
  })
}

test('mobile navigation traps keyboard focus and returns focus on Escape', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openProjects(page)
  const open = page.getByRole('button', { name: 'Open navigation', exact: true })
  await open.click()
  const close = page.locator('.sidebar').getByRole('button', { name: 'Close navigation', exact: true })
  await expect(close).toBeFocused()
  await page.keyboard.press('Shift+Tab')
  await expect(page.locator('.nav-configuration > summary')).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(close).toBeFocused()
  await page.locator('.nav-configuration > summary').click()
  await expect(page.getByRole('link', { name: 'Settings', exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(open).toBeFocused()
  await expect(page.getByRole('link', { name: 'Settings', exact: true })).not.toBeVisible()
  await open.click()
  await page.setViewportSize({ width: 1440, height: 900 })
  await expect(page.locator('.sidebar')).not.toHaveClass(/sidebar-open/)
})

test('project creation dialog stays readable and restores focus after dismissal', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openProjects(page)
  const open = page.getByRole('button', { name: 'New project', exact: true })
  await open.click()
  await expect(page.getByRole('textbox', { name: 'Name', exact: true })).toBeFocused()
  const dialog = page.getByRole('dialog')
  expect(await dialog.evaluate(element => element.getBoundingClientRect().right)).toBeLessThanOrEqual(390)
  await dialog.getByRole('button', { name: 'Create project', exact: true }).focus()
  await page.keyboard.press('Tab')
  await expect(dialog.getByRole('button', { name: 'Close', exact: true })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  await expect(open).toBeFocused()
})
