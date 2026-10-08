/**
 * The kaching.
 *
 * Synthesised with the Web Audio API rather than shipped as an audio file, so there is
 * no binary asset and nothing to load before it can play.
 *
 * Demure and mindful means: quiet (peak gain 0.09), short (about 420ms), and a soft
 * two-note bell rather than a cash-register clang. Two sine partials a fifth apart with
 * a fast attack and an exponential tail — closer to a shop doorbell than a slot machine.
 * It plays once per approval and never for a rejection, because a rejection is not a
 * small celebration.
 */

let context: AudioContext | null = null

function audio(): AudioContext | null {
  if (typeof window === 'undefined') return null
  if (!context) {
    const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
    if (!Ctor) return null
    context = new Ctor()
  }
  // Browsers suspend audio until a user gesture. An approval is a click, so this
  // resolves on the first one.
  if (context.state === 'suspended') void context.resume()
  return context
}

function chime(ctx: AudioContext, frequency: number, startAt: number, peak: number) {
  const osc = ctx.createOscillator()
  const gain = ctx.createGain()

  osc.type = 'sine'
  osc.frequency.setValueAtTime(frequency, startAt)

  gain.gain.setValueAtTime(0.0001, startAt)
  gain.gain.exponentialRampToValueAtTime(peak, startAt + 0.012)
  gain.gain.exponentialRampToValueAtTime(0.0001, startAt + 0.38)

  osc.connect(gain).connect(ctx.destination)
  osc.start(startAt)
  osc.stop(startAt + 0.42)
}

/** Play the approval sound. Silently does nothing where audio is unavailable. */
export function kaching() {
  const ctx = audio()
  if (!ctx) return

  const now = ctx.currentTime
  chime(ctx, 1318.5, now, 0.09)        // E6
  chime(ctx, 1975.5, now + 0.07, 0.06) // B6, a fifth above, slightly behind and softer
}
