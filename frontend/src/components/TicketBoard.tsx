/**
 * The board, grouped by status.
 *
 * Columns are fixed and shown even when empty, so a ticket moving from Open to Blocked
 * is a visible move across a stable layout rather than a card changing colour in place.
 *
 * The last column on every card is the run control: one button that starts a run, shows
 * "running…" while one is in flight, and reads "run again" once a ticket has been
 * through. Only one run per ticket at a time, which the backend enforces too.
 */

import type { Ticket, TicketStatus } from '../types'

const COLUMNS: { status: TicketStatus; label: string }[] = [
  { status: 'open', label: 'Open' },
  { status: 'in_progress', label: 'In progress' },
  { status: 'blocked', label: 'Blocked' },
  { status: 'resolved', label: 'Resolved' },
  { status: 'rejected', label: 'Rejected' },
]

interface Props {
  tickets: Ticket[]
  activeRunTicketId: number | null
  finishedTicketIds: number[]
  onStartRun: (ticketId: number) => void
  onOpenRun: (ticketId: number) => void
}

export function TicketBoard({
  tickets,
  activeRunTicketId,
  finishedTicketIds,
  onStartRun,
  onOpenRun,
}: Props) {
  return (
    <div className="board">
      {COLUMNS.map(({ status, label }) => {
        const group = tickets.filter(t => t.status === status)
        return (
          <section key={status} className={`board__column board__column--${status}`}>
            <header className="board__heading">
              <h3>{label}</h3>
              <span className="board__tally">{group.length}</span>
            </header>

            {group.length === 0 ? (
              <p className="board__empty">—</p>
            ) : (
              group.map(ticket => {
                const running = activeRunTicketId === ticket.id || Boolean(ticket.active_run_id)
                const finished = finishedTicketIds.includes(ticket.id)
                return (
                  <article key={ticket.id} className="ticket" data-ticket={ticket.id}>
                    <div className="ticket__top">
                      <span className="ticket__id">#{ticket.id}</span>
                      <span className="ticket__type">{ticket.type.replace(/_/g, ' ')}</span>
                    </div>

                    <h4 className="ticket__subject">{ticket.subject}</h4>
                    <p className="ticket__requester">{ticket.requester}</p>

                    {ticket.sku && (
                      <p className="ticket__detail">
                        {ticket.qty ?? 1} × {ticket.sku}
                        {ticket.size ? ` · ${ticket.size}` : ''}
                      </p>
                    )}

                    <div className="ticket__foot">
                      <span className="ticket__assignee">
                        {ticket.assignee ? `→ ${ticket.assignee}` : 'unassigned'}
                      </span>

                      <div className="ticket__controls">
                        {finished && !running && (
                          <button
                            type="button"
                            className="link-button"
                            onClick={() => onOpenRun(ticket.id)}
                          >
                            see what happened
                          </button>
                        )}
                        <button
                          type="button"
                          className={`run-button ${running ? 'is-running' : ''}`}
                          data-run-ticket={ticket.id}
                          disabled={running}
                          onClick={() => onStartRun(ticket.id)}
                        >
                          {running ? (
                            <>
                              <span className="pulse" aria-hidden="true" /> running…
                            </>
                          ) : finished ? (
                            'run again'
                          ) : (
                            'start run'
                          )}
                        </button>
                      </div>
                    </div>
                  </article>
                )
              })
            )}
          </section>
        )
      })}
    </div>
  )
}
