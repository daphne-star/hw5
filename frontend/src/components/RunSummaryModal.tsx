/**
 * What each agent did, shown once the run finishes.
 *
 * Grouped by agent rather than listed chronologically. The flat trace already exists in
 * the ticker; what this answers is a different question — "what was each of them
 * responsible for" — and that is hard to read off a timeline where three agents
 * interleave.
 *
 * Refusals are pulled out and shown in full, because a refusal is usually the most
 * important thing in a run and is easy to lose among forty tool calls.
 */

import { useEffect, useRef } from 'react'
import { AgentDoll } from './AgentDoll'
import type { RunSnapshot, TraceEntry } from '../types'

interface Props {
  runId: string
  ticketId: number
  entries: TraceEntry[]
  summary: RunSnapshot | null
  onClose: () => void
}

export function RunSummaryModal({ runId, ticketId, entries, summary, onClose }: Props) {
  const dialog = useRef<HTMLDivElement>(null)

  // Escape closes, and focus moves into the dialog so a keyboard user is not stranded
  // behind it on the board.
  useEffect(() => {
    dialog.current?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const agents = Array.from(new Set(entries.map(e => e.agent)))
  const refusals = entries.filter(e => e.kind === 'refusal')
  const limits = entries.filter(e => e.kind === 'limit')

  return (
    <div className="overlay" onClick={onClose} role="presentation">
      <div
        className="modal"
        ref={dialog}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={`What happened on ticket ${ticketId}`}
        onClick={event => event.stopPropagation()}
      >
        <header className="modal__head">
          <div>
            <h2>Ticket #{ticketId} — what each agent did</h2>
            <p className="modal__meta">
              {summary?.tool_calls_used ?? entries.filter(e => e.kind !== 'delegation').length} tool
              calls · {summary?.delegations_used ?? 0} handoffs · run {runId.slice(4, 12)}
            </p>
          </div>
          <button type="button" className="modal__close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        {summary?.handoffs && summary.handoffs.length > 0 && (
          <div className="chain">
            {summary.handoffs.map((hop, i) => (
              <span key={i} className="chain__hop">{hop.replace('->', '→')}</span>
            ))}
          </div>
        )}

        <div className="modal__body">
          {agents.map(agent => {
            const mine = entries.filter(e => e.agent === agent)
            const tools = mine.filter(e => e.kind === 'tool_call')
            const said = mine.filter(e => e.kind === 'message')
            const handed = mine.filter(e => e.kind === 'delegation')

            return (
              <section key={agent} className="recap">
                <div className="recap__who">
                  <AgentDoll agent={agent} state="done" size={56} />
                  <h3>{agent}</h3>
                </div>

                <div className="recap__what">
                  <p className="recap__stats">
                    {tools.length} tool {tools.length === 1 ? 'call' : 'calls'}
                    {handed.length > 0 &&
                      ` · asked ${handed.map(h => h.to_agent).join(', ')}`}
                  </p>

                  {tools.length > 0 && (
                    <ul className="recap__tools">
                      {tools.map(t => (
                        <li key={t.seq}>
                          <code>{t.tool}</code>
                        </li>
                      ))}
                    </ul>
                  )}

                  {said.length > 0 && (
                    <blockquote className="recap__said">{said[said.length - 1].text}</blockquote>
                  )}
                </div>
              </section>
            )
          })}
        </div>

        {(refusals.length > 0 || limits.length > 0) && (
          <div className="modal__refusals">
            <h3>Where the shop said no</h3>
            {refusals.map(r => (
              <p key={r.seq}>
                <strong>{r.agent}</strong> · <code>{r.tool}</code> —{' '}
                {r.result?.error?.message ?? r.error_code}
              </p>
            ))}
            {limits.map(l => (
              <p key={l.seq}>
                <strong>{l.agent}</strong> · limit <code>{l.limit}</code> — {l.detail}
              </p>
            ))}
          </div>
        )}

        {summary?.answer && (
          <footer className="modal__answer">
            <h3>Final call</h3>
            <p>{summary.answer}</p>
          </footer>
        )}
      </div>
    </div>
  )
}
