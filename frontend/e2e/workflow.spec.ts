import { expect, test } from '@playwright/test'

test('workspace exposes profile, radar, studio, and saved drafts', async ({ page }) => {
  await page.route('**/api/dashboard', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ open_roles: 0, qualified: 0, queued: 0, saved_drafts: 0, applied: 0 }) }))
  await page.route('**/api/manual-drafts', route => route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }))
  await page.goto('/')
  await expect(page.getByText('Drafts', { exact: true })).toBeVisible()
  await expect(page.getByText('Approval', { exact: true })).toHaveCount(0)
  await expect(page.getByText('Conversations', { exact: true })).toHaveCount(0)
  await page.getByRole('link', { name: 'Drafts' }).click()
  await expect(page.getByRole('heading', { name: 'Drafts' })).toBeVisible()
  await expect(page.getByText('No saved drafts')).toBeVisible()
})
