/**
 * The three panels along the bottom: approvals, cash, and reset.
 *
 * Approvals is first and widest because it is the only panel where a human is being
 * asked for something. Reset is last, smallest, and behind a confirm step, because it
 * is the one irreversible control on the page.
 */

import { useState } from 'react'
import { api } from '../api'
import { kaching } from '../sound'
import type { Approval, CashSummary, DecisionResponse } from '../types'

const money = (n: number) =>
  n.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 })

// Whoever is sitting at the desk. Recorded on every approval as `decided_by` and
// written into the payment record, so the audit trail names a person.
const DESK_OPERATOR = 'Daphne'

// ---------------------------------------------------------------------------

interface ApprovalsProps {
  approvals: Approval[]
  cash: CashSummary | null
  onDecided: (response: DecisionResponse) => void
}

export function ApprovalsQueue({ approvals, cash, onDecided }: ApprovalsProps) {
  const [busy, setBusy] = useState<number | null>(null)
  const [outcome, setOutcome] = useState<{ id: number; text: string; ok: boolean } | null>(null)

  const pending = approvals.filter(a => a.status === 'pending')
  const balance = cash?.accounts[0]?.balance ?? 0

  async function decide(approval: Approval, decision: 'approved' | 'rejected') {
    setBusy(approval.id)
    setOutcome(null)
    try {
      const response = await api.decide(approval.id, decision, DESK_OPERATOR)
      // Only on approval — a rejection is not a small celebration.
      if (decision === 'approved') kaching()

      const executed = response.executed
      const text = !executed
        ? 'Rejected. Nothing was paid.'
        : executed.result.ok
          ? `Done — ${executed.action?.replace(/_/g, ' ')}.`
          : `Approved, but refused: ${executed.result.error?.message ?? 'see trace'}`

      setOutcome({ id: approval.id, text, ok: executed ? executed.result.ok : true })
      onDecided(response)
    } catch (error) {
      setOutcome({ id: approval.id, text: String(error), ok: false })
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="panel panel--approvals">
      <header className="panel__head">
        <h3>Approvals</h3>
        <span className="panel__badge">{pending.length} waiting</span>
      </header>

      {pending.length === 0 ? (
        <p className="panel__empty">Nothing is waiting on you.</p>
      ) : (
        <ul className="approvals">
          {pending.map(approval => {
            const after = balance - approval.amount
            const short = after < 0
            return (
              <li key={approval.id} className="approval">
                <div className="approval__what">
                  <span className="approval__kind">{approval.kind.replace(/_/g, ' ')}</span>
                  <span className="approval__amount">{money(approval.amount)}</span>
                </div>

                <p className="approval__reason">{approval.reason}</p>

                <dl className="approval__facts">
                  <div>
                    <dt>from</dt>
                    <dd>{approval.account}</dd>
                  </div>
                  <div>
                    <dt>balance now</dt>
                    <dd>{money(balance)}</dd>
                  </div>
                  <div>
                    <dt>balance after</dt>
                    <dd className={short ? 'is-short' : 'is-after'}>{money(after)}</dd>
                  </div>
                </dl>

                {short && (
                  <p className="approval__warning">
                    This would overdraw the account. The shop will refuse it.
                  </p>
                )}

                <div className="approval__actions">
                  <button
                    type="button"
                    className="button button--approve"
                    disabled={busy === approval.id}
                    onClick={() => decide(approval, 'approved')}
                  >
                    {busy === approval.id ? 'working…' : 'Approve'}
                  </button>
                  <button
                    type="button"
                    className="button button--reject"
                    disabled={busy === approval.id}
                    onClick={() => decide(approval, 'rejected')}
                  >
                    Reject
                  </button>
                </div>

                {outcome?.id === approval.id && (
                  <p className={`approval__outcome ${outcome.ok ? '' : 'is-refused'}`}>
                    {outcome.text}
                  </p>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}

// ---------------------------------------------------------------------------

export function CashPanel({ cash }: { cash: CashSummary | null }) {
  if (!cash) return <section className="panel panel--cash"><h3>Cash</h3></section>

  const committed = cash.open_invoices_total + cash.upcoming_rent
  const left = cash.total_balance - committed

  return (
    <section className="panel panel--cash">
      <header className="panel__head">
        <h3>Cash</h3>
        <span className="panel__badge">as of {cash.as_of ?? '—'}</span>
      </header>

      <p className="cash__headline">{money(cash.total_balance)}</p>
      <p className="cash__caption">
        {cash.accounts.map(a => a.name).join(', ')} · cash only ever goes out
      </p>

      <dl className="cash__lines">
        <div>
          <dt>open invoices</dt>
          <dd>{money(cash.open_invoices_total)}</dd>
        </div>
        <div>
          <dt>rent coming</dt>
          <dd>{money(cash.upcoming_rent)}</dd>
        </div>
        <div className="cash__rule">
          <dt>left if both are paid</dt>
          <dd className={left < 0 ? 'is-short' : ''}>{money(left)}</dd>
        </div>
      </dl>
    </section>
  )
}

// ---------------------------------------------------------------------------

export function ResetPanel({ onReset }: { onReset: () => void }) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  async function doReset() {
    setBusy(true)
    try {
      const result = await api.reset()
      setMessage(result.note)
      onReset()
    } catch (error) {
      setMessage(String(error))
    } finally {
      setBusy(false)
      setConfirming(false)
    }
  }

  return (
    <section className="panel panel--reset">
      <header className="panel__head">
        <h3>Reset</h3>
      </header>

      {!confirming ? (
        <>
          <p className="panel__empty">
            Restores the shop to its original state so the tickets can be worked again.
          </p>
          <button type="button" className="button button--quiet" onClick={() => setConfirming(true)}>
            Reset the shop
          </button>
        </>
      ) : (
        <div className="confirm">
          <p>
            This discards every payment, approval, order, and draft the agents have made.
            The audit trail is kept.
          </p>
          <div className="confirm__actions">
            <button
              type="button"
              className="button button--danger"
              disabled={busy}
              onClick={doReset}
            >
              {busy ? 'resetting…' : 'Yes, reset'}
            </button>
            <button type="button" className="button button--quiet" onClick={() => setConfirming(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {message && <p className="panel__note">{message}</p>}
    </section>
  )
}
