import { test, expect } from '@playwright/test'

test('external inventory supports keyboard discovery and explicit verification', async ({ page, request }) => {
  const auth = await (await request.get('/api/v1/auth/config')).json()
  const adminHeaders: Record<string, string> = {}
  if (auth.mode === 'password') {
    const login = await request.post('/api/v1/auth/login', { data: {
      username: process.env.SPECTARR_E2E_USERNAME ?? 'release-admin',
      password: process.env.SPECTARR_E2E_PASSWORD ?? 'release-rehearsal-admin-password'
    } })
    expect(login.ok()).toBeTruthy()
    const token = (await login.json()).access_token as string
    adminHeaders.Authorization = `Bearer ${token}`
    await page.addInitScript(value => sessionStorage.setItem('spectarr_access_token', value), token)
  }
  const suffix = Date.now()
  const project = await (await request.post('/api/v1/projects', { headers: adminHeaders, data: { name: `External UI ${suffix}` } })).json()
  const agent = await (await request.post('/api/v1/agents/register', { headers: adminHeaders, data: { name: `External UI agent ${suffix}` } })).json()
  const root = await (await request.post('/api/v1/external-roots', { headers: adminHeaders, data: { project_id: project.id, agent_id: agent.id, label: 'Research archive', path: '/archive' } })).json()
  const task = await (await request.post(`/api/v1/external-roots/${root.id}/tasks`, { headers: adminHeaders, data: { kind: 'scan' } })).json()
  const headers = { Authorization: `Bearer ${agent.token}` }
  expect((await request.post(`/api/v1/external-agent/tasks/${task.id}/observations`, { headers, data: {
    sequence: 0, identity: 'ui-fixture', files: [{ path: 'sample.mgf', format: 'MGF', kind: 'file', signature: 'ui', byte_size: 42 }]
  } })).ok()).toBeTruthy()
  expect((await request.post(`/api/v1/external-agent/tasks/${task.id}/result`, { headers, data: { status: 'complete', identity: 'ui-fixture' } })).ok()).toBeTruthy()
  await page.goto(`/projects/${project.id}/external`)
  await expect(page.getByRole('heading', { name: 'External files', exact: true })).toBeVisible()
  const acquisition = page.getByRole('button', { name: 'sample.mgf' })
  await acquisition.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByText(/This path belongs to the named machine/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Import verified revision' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Verify content' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Verification requested' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Scan Research archive' })).toBeDisabled()
  await page.getByText('Folder status and recent operations', { exact: true }).click()
  await page.getByRole('button', { name: 'Cancel request' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Request canceled' })).toBeVisible()
  for (const width of [390, 1440]) {
    await page.setViewportSize({ width, height: 1000 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  }
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }))
  await page.waitForFunction(() => window.scrollY === 0)
  await page.screenshot({ path: `/tmp/spectarr-external-ui-${suffix}.png`, fullPage: true })
})
