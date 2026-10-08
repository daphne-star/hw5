/**
 * The dashboard.
 *
 * Top: the ticket board, grouped by status, with a run control on every card.
 * Middle: the five agents, showing what each is doing while a run is in flight.
 * Bottom: approvals, cash, and reset.
 *
 * One source of truth for shop state — the backend. Everything here refetches after a
 * run finishes or an approval is decided, rather than guessing at the new state
 * locally. A ticket's status is whatever the Boss wrote, not what the UI assumed.
 */

import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { AgentStage } from './components/AgentStage'
import { ApprovalsQueue, CashPanel, ResetPanel } from './components/BottomRow'
import { RunSummaryModal } from './components/RunSummaryModal'
import { TicketBoard } from './components/TicketBoard'
import { useRun } from './hooks/useRun'
import type { Approval, CashSummary, RunSnapshot, Ticket, TraceEntry } from './types'

interface FinishedRun {
  runId: string
  ticketId: number
  entries: TraceEntry[]
  summary: RunSnapshot | null
}

export default function App() {
  const [tickets, setTickets] = useState<Ticket[]>([])
  const [cash, setCash] = useState<CashSummary | null>(null)
  const [approvals, setApprovals] = useState<Approval[]>([])
  const [health, setHealth] = useState<{ model: string; portkey_key_loaded: boolean } | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [activeRun, setActiveRun] = useState<{ runId: string; ticketId: number } | null>(null)
  const [finished, setFinished] = useState<Record<number, FinishedRun>>({})
  const [showing, setShowing] = useState<number | null>(null)

  const live = useRun(activeRun?.runId ?? null, activeRun?.ticketId ?? null)

  const refresh = useCallback(async () => {
    try {
      const [t, c, a] = await Promise.all([api.tickets(), api.cash(), api.approvals()])
      setTickets(t.tickets)
      setCash(c)
      setApprovals(a.approvals)
      setError(null)
    } catch (e) {
      setError(
        `Cannot reach the backend at ${api.base}. Start it with ` +
          `\`uvicorn backend.main:app --port 8000\`.`,
      )
      void e
    }
  }, [])

  useEffect(() => {
    void refresh()
    api.health().then(setHealth).catch(() => undefined)
  }, [refresh])

  // When a run ends: keep its trace for the recap, open it, and refetch so the board
  // shows the status the Boss actually wrote.
  useEffect(() => {
    if (!live || !activeRun) return
    if (live.status !== 'done' && live.status !== 'failed') return

    setFinished(previous => ({
      ...previous,
      [activeRun.ticketId]: {
        runId: activeRun.runId,
        ticketId: activeRun.ticketId,
        entries: live.entries,
        summary: live.summary,
      },
    }))
    setShowing(activeRun.ticketId)
    setActiveRun(null)
    void refresh()
  }, [live, activeRun, refresh])

  async function startRun(ticketId: number) {
    try {
      const started = await api.startRun(ticketId)
      setActiveRun({ runId: started.run_id, ticketId })
      setShowing(null)
      setFinished(previous => {
        const next = { ...previous }
        delete next[ticketId]
        return next
      })
    } catch (e) {
      setError(String(e))
    }
  }

  const runningTicket = activeRun?.ticketId ?? null
  const recap = showing !== null ? finished[showing] : undefined

  return (
    <div className="app">
      <header className="masthead">
        <div>
          <h1>Campus Customs</h1>
          <p className="masthead__sub">Multi-agent shop operations · shop date {cash?.as_of ?? '…'}</p>
        </div>
        {health && (
          <p className="masthead__status">
            <span className={`dot ${health.portkey_key_loaded ? 'is-ok' : 'is-off'}`} />
            {health.model} · key {health.portkey_key_loaded ? 'loaded on the server' : 'missing'}
          </p>
        )}
      </header>

      {error && <p className="banner">{error}</p>}

      <section className="section">
        <div className="section__head">
          <h2>The board</h2>
          <span className="section__hint">
            {tickets.length} tickets · pick one and start a run
          </span>
        </div>
        <TicketBoard
          tickets={tickets}
          activeRunTicketId={runningTicket}
          finishedTicketIds={Object.keys(finished).map(Number)}
          onStartRun={startRun}
          onOpenRun={setShowing}
        />
      </section>

      <section className="section">
        <div className="section__head">
          <h2>The team</h2>
          <span className="section__hint">
            {live
              ? `working ticket #${live.ticketId} · ${live.entries.length} steps${
                  live.transport === 'poll' ? ' · polling' : ''
                }`
              : 'idle'}
          </span>
        </div>

        <AgentStage entries={live?.entries ?? []} running={live?.status === 'running'} />

        {live && live.entries.length > 0 && (
          <ol className="ticker">
            {live.entries.slice(-7).map(entry => (
              <li key={entry.seq} className={`ticker__row ticker__row--${entry.kind}`}>
                <span className="ticker__agent">{entry.agent}</span>
                <span className="ticker__what">
                  {entry.kind === 'delegation'
                    ? `→ ${entry.to_agent}`
                    : entry.kind === 'message'
                      ? 'reported back'
                      : entry.tool ?? entry.kind}
                </span>
              </li>
            ))}
          </ol>
        )}
      </section>

      <section className="row">
        <ApprovalsQueue
          approvals={approvals}
          cash={cash}
          onDecided={() => {
            void refresh()
          }}
        />
        <CashPanel cash={cash} />
        <ResetPanel
          onReset={() => {
            setFinished({})
            setShowing(null)
            void refresh()
          }}
        />
      </section>

      {recap && (
        <RunSummaryModal
          runId={recap.runId}
          ticketId={recap.ticketId}
          entries={recap.entries}
          summary={recap.summary}
          onClose={() => setShowing(null)}
        />
      )}
    </div>
  )
}
