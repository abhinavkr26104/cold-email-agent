import { expect, test } from '@playwright/test'

const profile = { candidate_name: 'Ada', candidate_profile: 'Python API developer', preferences: { desired_titles: ['Engineer'], minimum_score: 70 } }
const source = { id: 1, company_name: 'Acme', provider: 'lever', board_token: 'acme', board_url: 'https://jobs.lever.co/acme', enabled: 1 }

test('profile to discovery, application, approval, send, and reply', async ({ page }) => {
  let applied = false, sent = false
  const approvals = Array.from({ length: 5 }, (_, index) => ({
    id: index + 10, job_id: index + 1, title: `Python Engineer ${index + 1}`, company_name: 'Acme', location: 'Remote', score: 95 - index,
    subject: `Engineer ${index + 1}`, body: 'Hello Rina,\n\nA grounded outreach message.\n\nAda', edited: 0, stale: 0,
    display_state: sent && index === 0 ? 'Sent' : applied ? 'Ready to send' : 'Ready after application', can_send: applied && !sent,
    blockers: applied ? [] : [{ code: 'application_required', message: 'Mark this role as applied.' }], application_status: applied ? 'applied' : 'discovered',
    apply_url: 'https://acme.test/apply', job_url: 'https://acme.test/job', selected_contact_email: 'rina@acme.test', contact_name: 'Rina', contact_position: 'Recruiter',
    contact_confidence: 95, contact_source_kind: 'hunter', contact_sources: ['https://acme.test/team'], evidence: ['Python APIs'], missing: [],
  }))
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url()), method = route.request().method()
    let body: unknown = {}
    if (url.pathname === '/api/profile') body = profile
    else if (url.pathname === '/api/sources') body = method === 'POST' ? source : [source]
    else if (url.pathname === '/api/dashboard') body = { open_roles: 5, qualified: 5, applied: applied ? 1 : 0, queued: 5, sent: sent ? 1 : 0, replies: sent ? 1 : 0 }
    else if (url.pathname === '/api/discovery-runs' && method === 'POST') body = { id: 'run-1', status: 'queued', stage: 'queued', progress: 0, result: {}, errors: [] }
    else if (url.pathname === '/api/discovery-runs/run-1') body = { id: 'run-1', status: 'completed', stage: 'completed', progress: 100, result: { drafts_prepared: 5 }, errors: [] }
    else if (url.pathname === '/api/approval-items') body = approvals.map((item,index) => ({...item, display_state: sent&&index===0?'Sent':applied?'Ready to send':'Ready after application', can_send:applied&&!sent, application_status:applied?'applied':'discovered', blockers:applied?[]:[{code:'application_required',message:'Mark this role as applied.'}]}))
    else if (url.pathname.endsWith('/mark-applied')) { applied = true; body = { application_status: 'applied' } }
    else if (url.pathname === '/api/outreach/send') { sent = true; body = { sent: [1], skipped: [], failed: [] } }
    else if (url.pathname === '/api/conversations') body = sent ? [{ id: 1, title: 'Python Engineer 1', company_name: 'Acme', recipient: 'rina@acme.test', status: 'replied', sent_at: '2026-08-15T10:00:00Z', gmail_thread_id: 'thread-1' }] : []
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  })

  await page.goto('/profile')
  await expect(page.getByRole('heading', { name: 'Your search profile' })).toBeVisible()
  await page.getByRole('link', { name: /Overview/ }).click()
  await page.getByRole('button', { name: 'Run discovery' }).click()
  await expect(page.getByText('completed', { exact: true })).toBeVisible()
  await page.getByRole('link', { name: 'Approval' }).click()
  await expect(page.getByRole('heading', { name: 'Application-first outreach' })).toBeVisible()
  await expect(page.getByText('Python Engineer 5')).toBeVisible()
  await page.getByRole('button', { name: 'Mark applied' }).first().click()
  await page.getByRole('checkbox', { name: 'Include in batch' }).first().check()
  await page.getByRole('button', { name: 'Send selected (1)' }).click()
  await page.getByRole('button', { name: 'Confirm and send' }).click()
  await expect(page.getByRole('dialog', { name: 'Confirm batch send' })).toBeHidden()
  await page.getByRole('link', { name: 'Conversations' }).click()
  await expect(page.getByRole('heading', { name: 'Conversations' })).toBeVisible()
  await expect(page.getByText('rina@acme.test', { exact: true })).toBeVisible()
  await expect(page.getByText('replied')).toBeVisible()
})
