/**
 * Every call the dashboard makes.
 *
 * There are no credentials here, and there is nothing to put here: the Portkey key
 * lives in the backend process and never crosses this boundary. The only configuration
 * is a base URL, which defaults to the local backend.
 */

import type { Approval, CashSummary, DecisionResponse, RunSnapshot, Ticket } from './types'

const BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE}${path}`)
  if (!response.ok) throw new Error(`GET ${path} failed: ${response.status}`)
  return response.json() as Promise<T>
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = (payload as { detail?: unknown }).detail
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail ?? payload))
  }
  return payload as T
}

export const api = {
  base: BASE,

  health: () => get<{ model: string; portkey_key_loaded: boolean; agents: string[] }>('/api/health'),

  tickets: () => get<{ tickets: Ticket[]; count: number }>('/api/tickets'),

  cash: () => get<CashSummary>('/api/cash'),

  approvals: () => get<{ approvals: Approval[]; pending_count: number }>('/api/approvals'),

  run: (runId: string, since = 0) => get<RunSnapshot>(`/api/runs/${runId}?since=${since}`),

  startRun: (ticketId: number, agent?: string) =>
    post<{ run_id: string; ticket_id: number; mode: string }>(
      `/api/tickets/${ticketId}/run`,
      agent ? { agent } : {},
    ),

  decide: (approvalId: number, decision: 'approved' | 'rejected', decidedBy: string) =>
    post<DecisionResponse>(`/api/approvals/${approvalId}/decision`, {
      decision,
      decided_by: decidedBy,
    }),

  reset: () => post<{ ok: boolean; note: string }>('/api/reset', { confirm: true }),

  /** SSE endpoint for a run. The browser opens it; we only build the URL. */
  streamUrl: (runId: string, since = 0) => `${BASE}/api/runs/${runId}/stream?since=${since}`,
}
