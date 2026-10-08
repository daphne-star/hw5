/**
 * The five agents, side by side, showing what each is saying or doing right now.
 *
 * The trace is a flat ordered list; this turns it into per-agent state. The last entry
 * belonging to an agent decides her pose and the line under her, so a glance across the
 * row answers "who is working, and on what" without reading the log.
 */

import { AgentDoll, type DollState } from './AgentDoll'
import type { TraceEntry } from '../types'

export const TEAM = ['Boss', 'Inventory', 'Accounting', 'Facilities', 'Customer Service'] as const

interface Props {
  entries: TraceEntry[]
  running: boolean
}

interface AgentView {
  state: DollState
  line: string
  acted: boolean
}

/** Plain-language description of one trace entry, from that agent's point of view. */
function describe(entry: TraceEntry): string {
  switch (entry.kind) {
    case 'delegation':
      return `handing it to ${entry.to_agent}`
    case 'tool_call':
      return `${readableTool(entry.tool ?? '')}`
    case 'refusal':
      return `refused: ${entry.error_code?.replace(/_/g, ' ') ?? 'blocked'}`
    case 'limit':
      return `stopped: ${entry.limit?.replace(/_/g, ' ') ?? 'limit'}`
    case 'error':
      return 'something went wrong'
    case 'message':
      return firstSentence(entry.text ?? '')
    default:
      return ''
  }
}

function readableTool(tool: string): string {
  const words: Record<string, string> = {
    get_today: "checking the shop's date",
    list_tickets: 'reading the board',
    get_ticket: 'reading the ticket',
    get_inventory: 'counting stock',
    get_pricing: 'checking prices',
    list_vendors: 'looking up vendors',
    list_leases: 'reading the lease',
    get_cash_accounts: 'checking the balance',
    list_invoices: 'checking what is owed',
    list_payments: 'reviewing payments',
    list_approvals: 'checking approvals',
    list_purchase_orders: 'checking orders',
    list_drafts: 'checking drafts',
    request_approval: 'asking for approval',
    record_approval_decision: 'recording a decision',
    make_payment: 'paying',
    create_purchase_order: 'placing an order',
    save_draft_message: 'drafting a message',
    set_ticket_status: 'setting the status',
    assign_ticket: 'assigning the ticket',
    add_ticket_note: 'writing a note',
  }
  return words[tool] ?? tool.replace(/_/g, ' ')
}

function firstSentence(text: string): string {
  const flat = text.replace(/[*#`]/g, '').replace(/\s+/g, ' ').trim()
  const stop = flat.search(/[.!?](\s|$)/)
  const sentence = stop > 0 ? flat.slice(0, stop + 1) : flat
  return sentence.length > 120 ? `${sentence.slice(0, 117)}…` : sentence
}

function viewFor(agent: string, entries: TraceEntry[], running: boolean): AgentView {
  const mine = entries.filter(e => e.agent === agent)
  const incoming = entries.filter(e => e.kind === 'delegation' && e.to_agent === agent)

  if (mine.length === 0) {
    return incoming.length > 0
      ? { state: 'working', line: 'just been asked to help', acted: true }
      : { state: 'idle', line: running ? 'waiting' : 'at the counter', acted: false }
  }

  const last = mine[mine.length - 1]
  const isLatestOverall = entries[entries.length - 1]?.agent === agent

  let state: DollState = 'idle'
  if (last.kind === 'refusal' || last.kind === 'limit' || last.kind === 'error') state = 'blocked'
  else if (!running) state = 'done'
  else if (!isLatestOverall) state = 'idle'
  else if (last.kind === 'message') state = 'speaking'
  else state = 'working'

  return { state, line: describe(last), acted: true }
}

export function AgentStage({ entries, running }: Props) {
  return (
    <div className="stage">
      {TEAM.map(agent => {
        const view = viewFor(agent, entries, running)
        const count = entries.filter(e => e.agent === agent).length
        return (
          <figure key={agent} className={`stage__agent ${view.acted ? '' : 'is-quiet'}`}>
            <AgentDoll agent={agent} state={view.state} />
            <figcaption>
              <span className="stage__name">{agent}</span>
              <span className={`bubble bubble--${view.state}`}>{view.line}</span>
              {count > 0 && <span className="stage__count">{count} steps</span>}
            </figcaption>
          </figure>
        )
      })}
    </div>
  )
}
