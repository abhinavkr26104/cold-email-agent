import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import App from './App'

function response(body: unknown, status = 200) {
  return Promise.resolve({ ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) } as Response)
}
function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><App/></MemoryRouter></QueryClientProvider>)
}
afterEach(() => vi.restoreAllMocks())

test('does not render removed approval or conversation navigation', () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(() => response({ open_roles: 0, qualified: 0, queued: 0, saved_drafts: 0, applied: 0 }))
  renderAt('/')
  expect(screen.queryByText('Approval')).not.toBeInTheDocument()
  expect(screen.queryByText('Conversations')).not.toBeInTheDocument()
  expect(screen.getByText('Drafts')).toBeInTheDocument()
})

test('lists and edits saved drafts', async () => {
  const draft = { id: 4, candidate_name: 'Ada', company_name: 'Acme', role_title: 'Engineer', recipient_name: 'Rina', recipient_position: 'Recruiter', candidate_profile: 'Python developer', job_description: 'Build APIs', body: 'Hello Rina', created_at: '2026-08-16T10:00:00Z', updated_at: '2026-08-16T10:00:00Z' }
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
    if (String(input) === '/api/manual-drafts/4' && init?.method === 'PATCH') return response({ ...draft, body: 'Updated message' })
    return response([draft])
  })
  renderAt('/drafts')
  const message = await screen.findByLabelText('Email for Engineer')
  fireEvent.change(message, { target: { value: 'Updated message' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/manual-drafts/4', expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ body: 'Updated message' }) })))
})

test('restores and saves every matching preference', async () => {
  const profile = { candidate_name: 'Ada', candidate_profile: 'Python developer', preferences: { desired_titles: ['Engineer'], locations: ['Remote'], employment_types: ['Full-time'], seniority: ['Mid'], required_keywords: ['Python'], excluded_keywords: ['Manager'], remote_policy: 'remote only', minimum_score: 82 } }
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
    if (String(input) === '/api/profile' && init?.method === 'PUT') return response(profile)
    if (String(input) === '/api/profile') return response(profile)
    return response([])
  })
  renderAt('/profile')
  expect(await screen.findByDisplayValue('Remote')).toBeInTheDocument()
  expect(screen.getByDisplayValue('Full-time')).toBeInTheDocument()
  expect(screen.getByDisplayValue('Mid')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Locations'), { target: { value: 'Bengaluru, Remote' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save profile' }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/profile', expect.objectContaining({ method: 'PUT', body: JSON.stringify({ candidate_name: 'Ada', candidate_profile: 'Python developer', preferences: { ...profile.preferences, desired_titles: ['Engineer'], locations: ['Bengaluru', 'Remote'], employment_types: ['Full-time'], seniority: ['Mid'], required_keywords: ['Python'], excluded_keywords: ['Manager'], remote_policy: 'remote only', minimum_score: 82 } }) })))
})

test('starts and displays a completed Radar discovery run', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
    if (String(input) === '/api/discovery-runs' && init?.method === 'POST') return response({ id: 'run-1', status: 'queued', stage: 'queued', progress: 0, result: {}, errors: [] }, 202)
    if (String(input) === '/api/discovery-runs/run-1') return response({ id: 'run-1', status: 'completed', stage: 'completed', progress: 100, result: {}, errors: [] })
    return response({ items: [], total: 0 })
  })
  renderAt('/radar')
  await screen.findByText('No roles on radar')
  fireEvent.click(screen.getByRole('button', { name: 'Search opportunities' }))
  expect(await screen.findByText('Discovery completed')).toBeInTheDocument()
  expect(fetchMock).toHaveBeenCalledWith('/api/discovery-runs', expect.objectContaining({ method: 'POST' }))
  expect(fetchMock).toHaveBeenCalledWith('/api/discovery-runs/run-1', expect.anything())
})
