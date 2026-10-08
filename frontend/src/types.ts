/** Shapes the backend returns. Kept thin — only what the dashboard actually reads. */

export type TicketStatus = 'open' | 'in_progress' | 'blocked' | 'resolved' | 'rejected'

export interface Ticket {
  id: number
  type: string
  requester: string
  subject: string
  sku: string | null
  size: string | null
  qty: number | null
  status: TicketStatus
  notes: string | null
  assignee: string | null
  created_at: string
  active_run_id: string | null
}

export type EntryKind = 'tool_call' | 'refusal' | 'delegation' | 'message' | 'limit' | 'error'

export interface TraceEntry {
  seq: number
  at: string
  agent: string
  depth: number
  kind: EntryKind
  tool?: string
  arguments?: Record<string, unknown>
  result?: { ok: boolean; error?: { code: string; message: string } }
  error_code?: string | null
  to_agent?: string
  task?: string
  text?: string
  limit?: string
  detail?: string
}

export type RunStatus = 'queued' | 'running' | 'done' | 'failed'

export interface RunSnapshot {
  run_id: string
  ticket_id: number
  mode: string
  agent: string
  status: RunStatus
  answer: string | null
  error: string | null
  changes: Record<string, unknown> | null
  agents_involved: string[]
  handoffs: string[]
  tool_calls_used: number
  delegations_used: number
  last_seq: number
  entries: TraceEntry[]
}

export interface Approval {
  id: number
  kind: 'invoice' | 'rent' | 'purchase_order'
  ref_id: number | null
  amount: number
  account: string
  reason: string
  requested_by: string
  ticket_id: number | null
  status: 'pending' | 'approved' | 'rejected'
  decided_by: string | null
  decided_at: string | null
  consumed_by_payment_id: number | null
  details: Record<string, unknown> | null
}

export interface CashSummary {
  accounts: { name: string; balance: number; date: string }[]
  total_balance: number
  open_invoices_total: number
  upcoming_rent: number
  as_of: string | null
}

export interface DecisionResponse {
  approval: Approval
  executed: {
    action: string | null
    result: {
      ok: boolean
      data?: Record<string, unknown>
      error?: { code: string; message: string }
    }
  } | null
}
