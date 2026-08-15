import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import App from './App'

const approval = {
  id: 7, job_id: 4, title: 'Python Engineer', company_name: 'Acme', location: 'Remote',
  score: 92, subject: 'Python role', body: 'Hello recruiting team', edited: 0, stale: 0,
  display_state: 'Ready to send', can_send: true, blockers: [], application_status: 'applied',
  apply_url: 'https://acme.test/apply', job_url: 'https://acme.test/job',
  selected_contact_email: 'recruiter@acme.test', contact_name: 'Rina', contact_position: 'Recruiter',
  contact_confidence: 94, contact_source_kind: 'hunter', contact_sources: ['https://acme.test/team'],
  evidence: ['Python'], missing: [],
}

function response(body: unknown, status = 200) {
  return Promise.resolve({ ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) } as Response)
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><App/></MemoryRouter></QueryClientProvider>)
}

afterEach(() => vi.restoreAllMocks())

test('renders the radar empty state', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(() => response({ items: [], total: 0 }))
  renderAt('/radar')
  expect(await screen.findByText('No roles on radar')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Opportunity radar' })).toBeInTheDocument()
})

test('edits and saves an approval draft', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
    if (String(input).includes('/api/drafts/7') && init?.method === 'PATCH') return response({...approval, body: 'Updated grounded message', edited: 1})
    return response([approval])
  })
  renderAt('/approval')
  const message = await screen.findByLabelText('Message')
  fireEvent.change(message, { target: { value: 'Updated grounded message' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/drafts/7', expect.objectContaining({
    method: 'PATCH', body: JSON.stringify({subject:'Python role', body:'Updated grounded message'}),
  })))
})

test('requires a visible confirmation before sending a batch', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
    if (String(input) === '/api/outreach/send') return response({ sent: [1], skipped: [], failed: [] })
    return response([approval])
  })
  renderAt('/approval')
  fireEvent.click(await screen.findByRole('checkbox', { name: 'Include in batch' }))
  fireEvent.click(screen.getByRole('button', { name: 'Send selected (1)' }))
  expect(screen.getByRole('dialog', { name: 'Confirm batch send' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Confirm and send' }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/outreach/send', expect.objectContaining({
    method: 'POST', body: JSON.stringify({draft_ids:[7],confirmed:true}),
  })))
})
