export type Profile = {
  candidate_name: string
  candidate_profile: string
  preferences: Record<string, unknown>
}

export type Source = {
  id: number; company_name: string; provider: string; board_token: string
  board_url: string; enabled: number
}

export type DiscoveryRun = {
  id: string; status: 'queued' | 'running' | 'completed' | 'partial' | 'failed' | 'interrupted'
  stage: string; progress: number; result: Record<string, unknown>
  errors: { stage: string; message: string }[]
}

export type Match = {
  id: number; title: string; company_name: string; location: string; score: number
  decision: string; evidence: string[]; missing: string[]; rejection_reason?: string
  apply_url: string; job_url: string; status: string; application_status: string
  draft_id?: number; draft_status?: string; selected_contact_email?: string
}

export type Blocker = { code: string; message: string }
export type ApprovalItem = {
  id: number; job_id: number; title: string; company_name: string; location: string
  score: number; subject: string; body: string; edited: number; stale: number
  display_state: 'Draft ready' | 'Contact pending' | 'Ready after application' | 'Ready to send' | 'Sent'
  can_send: boolean; blockers: Blocker[]; application_status: string; applied_at?: string
  apply_url: string; job_url: string; selected_contact_email?: string; contact_name?: string
  contact_position?: string; contact_confidence?: number; contact_source_kind?: string
  contact_sources: string[]; evidence: string[]; missing: string[]
}

export type Dashboard = {
  open_roles: number; qualified: number; queued: number; sent: number; replies: number; applied: number
}

export type Settings = {
  gmail: { connected: boolean; client_configured: boolean }
  providers: Record<string, boolean>
  quotas: Record<string, { used: number; limit: number }>
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) { super(message); this.status = status }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    const detail = payload.detail
    throw new ApiError(response.status, typeof detail === 'string' ? detail : detail?.message || 'Request failed')
  }
  return response.status === 204 ? undefined as T : response.json()
}

export const api = {
  profile: () => request<Profile | null>('/api/profile'),
  saveProfile: (data: Profile) => request<Profile>('/api/profile', { method: 'PUT', body: JSON.stringify(data) }),
  sources: () => request<Source[]>('/api/sources'),
  addSource: (data: { company_name: string; board_url: string }) => request<Source>('/api/sources', { method: 'POST', body: JSON.stringify(data) }),
  patchSource: (id: number, data: Partial<Source>) => request<Source>(`/api/sources/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  dashboard: () => request<Dashboard>('/api/dashboard'),
  startDiscovery: () => request<DiscoveryRun>('/api/discovery-runs', { method: 'POST' }),
  discoveryRun: (id: string) => request<DiscoveryRun>(`/api/discovery-runs/${id}`),
  matches: (query = '') => request<{ items: Match[]; total: number }>(`/api/matches${query}`),
  approvals: () => request<ApprovalItem[]>('/api/approval-items'),
  saveDraft: (id: number, data: { subject: string; body: string }) => request<ApprovalItem>(`/api/drafts/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  regenerate: (id: number) => request<ApprovalItem>(`/api/drafts/${id}/regenerate`, { method: 'POST' }),
  markApplied: (id: number) => request(`/api/jobs/${id}/mark-applied`, { method: 'POST' }),
  findContact: (id: number) => request(`/api/jobs/${id}/find-contact`, { method: 'POST' }),
  send: (ids: number[]) => request<{ sent: number[]; skipped: unknown[]; failed: unknown[] }>('/api/outreach/send', { method: 'POST', body: JSON.stringify({ draft_ids: ids, confirmed: true }) }),
  conversations: () => request<Record<string, unknown>[]>('/api/conversations'),
  syncReplies: () => request<Record<string, number>>('/api/replies/sync', { method: 'POST' }),
  connectGmail: () => request('/api/gmail/connect', { method: 'POST' }),
  settings: () => request<Settings>('/api/settings/status'),
  manualDraft: (data: Record<string, string>) => request<{ body: string }>('/api/manual-draft', { method: 'POST', body: JSON.stringify(data) }),
  extractDocument: async (file: File) => {
    const bytes = new Uint8Array(await file.arrayBuffer())
    let binary = ''
    for (let index = 0; index < bytes.length; index += 0x8000) binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000))
    return request<{ text: string }>('/api/documents/extract', { method: 'POST', body: JSON.stringify({ filename: file.name, content_base64: btoa(binary) }) })
  },
}
