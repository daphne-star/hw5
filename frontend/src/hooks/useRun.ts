/**
 * Watches one run as it happens.
 *
 * Server-sent events are the primary channel — the backend pushes each trace entry as
 * the agents produce it. If the stream cannot be opened (a proxy that buffers, a
 * browser that drops it), it falls back to polling `?since=` with the last sequence
 * number seen, which returns only what has not arrived yet. Either way the component
 * above sees the same growing list of entries.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { RunSnapshot, RunStatus, TraceEntry } from '../types'

export interface LiveRun {
  runId: string
  ticketId: number
  status: RunStatus
  entries: TraceEntry[]
  summary: RunSnapshot | null
  transport: 'stream' | 'poll'
}

export function useRun(runId: string | null, ticketId: number | null) {
  const [entries, setEntries] = useState<TraceEntry[]>([])
  const [status, setStatus] = useState<RunStatus>('queued')
  const [summary, setSummary] = useState<RunSnapshot | null>(null)
  const [transport, setTransport] = useState<'stream' | 'poll'>('stream')
  const lastSeq = useRef(0)

  const append = useCallback((incoming: TraceEntry[]) => {
    if (incoming.length === 0) return
    setEntries(previous => {
      const seen = new Set(previous.map(e => e.seq))
      const fresh = incoming.filter(e => !seen.has(e.seq))
      if (fresh.length === 0) return previous
      for (const entry of fresh) lastSeq.current = Math.max(lastSeq.current, entry.seq)
      return [...previous, ...fresh].sort((a, b) => a.seq - b.seq)
    })
  }, [])

  useEffect(() => {
    if (!runId) {
      setEntries([])
      setSummary(null)
      setStatus('queued')
      lastSeq.current = 0
      return
    }

    let cancelled = false
    let poller: number | undefined
    const source = new EventSource(api.streamUrl(runId))

    source.addEventListener('entry', event => {
      append([JSON.parse((event as MessageEvent).data) as TraceEntry])
      setStatus('running')
    })

    source.addEventListener('done', event => {
      const final = JSON.parse((event as MessageEvent).data) as RunSnapshot
      setSummary(final)
      setStatus(final.status)
      source.close()
    })

    source.onerror = () => {
      // The stream is gone. Fall back to polling rather than leaving the UI frozen.
      source.close()
      if (cancelled) return
      setTransport('poll')
      poller = window.setInterval(async () => {
        try {
          const snapshot = await api.run(runId, lastSeq.current)
          append(snapshot.entries)
          setStatus(snapshot.status)
          if (snapshot.status === 'done' || snapshot.status === 'failed') {
            setSummary(snapshot)
            window.clearInterval(poller)
          }
        } catch {
          /* keep trying; the backend may be restarting */
        }
      }, 700)
    }

    return () => {
      cancelled = true
      source.close()
      if (poller) window.clearInterval(poller)
    }
  }, [runId, append])

  const live: LiveRun | null = runId
    ? { runId, ticketId: ticketId ?? 0, status, entries, summary, transport }
    : null

  return live
}
