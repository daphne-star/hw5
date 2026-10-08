/**
 * A 2D cartoon doll for each agent, drawn as inline SVG.
 *
 * American-girl-doll proportions: a large round head, small body, flat colour fill, no
 * gradients or shading. Each agent gets a different pastel outfit, hair colour, and one
 * prop that says what they do — a clipboard for Inventory, a ledger for Accounting.
 * The props are what make five near-identical figures readable at a glance.
 *
 * Expression follows state. Eyes are simple arcs; a working doll's eyebrows lift and a
 * small activity ring turns beside her. Nothing bounces or wobbles — the point is to
 * show who is busy, not to be a toy.
 */

export type DollState = 'idle' | 'working' | 'speaking' | 'blocked' | 'done'

export interface DollPalette {
  dress: string
  dressShade: string
  hair: string
  skin: string
  accent: string
}

export const PALETTES: Record<string, DollPalette> = {
  Boss: { dress: '#d9c9e8', dressShade: '#c4b0da', hair: '#6b4f3f', skin: '#f0d7c4', accent: '#8f74b5' },
  Inventory: { dress: '#c2dcc9', dressShade: '#a9cbb3', hair: '#2f2a28', skin: '#d6a781', accent: '#5f9471' },
  Accounting: { dress: '#bcd3e8', dressShade: '#a2bfdb', hair: '#c98a3e', skin: '#f3dcc8', accent: '#5b84ad' },
  Facilities: { dress: '#e8d7bb', dressShade: '#d9c39c', hair: '#8c6239', skin: '#c08a5e', accent: '#a9834a' },
  'Customer Service': { dress: '#f0cdd6', dressShade: '#e3b2c0', hair: '#4a3b55', skin: '#eccbb3', accent: '#bf7e94' },
}

interface Props {
  agent: string
  state: DollState
  size?: number
}

export function AgentDoll({ agent, state, size = 96 }: Props) {
  const p = PALETTES[agent] ?? PALETTES.Boss
  const busy = state === 'working' || state === 'speaking'

  return (
    <svg
      width={size}
      height={size * 1.25}
      viewBox="0 0 80 100"
      role="img"
      aria-label={`${agent}, ${state}`}
      className={`doll doll--${state}`}
    >
      {/* activity ring — turns only while the agent is actually doing something */}
      {busy && (
        <circle
          cx="40" cy="32" r="29"
          fill="none" stroke={p.accent} strokeWidth="1.5"
          strokeDasharray="6 10" strokeLinecap="round" opacity="0.65"
          className="doll__ring"
        />
      )}

      {/* body */}
      <path d={`M40 56 C 26 56, 20 70, 19 92 L 61 92 C 60 70, 54 56, 40 56 Z`} fill={p.dress} />
      <path d={`M40 56 C 33 56, 30 64, 29 92 L 51 92 C 50 64, 47 56, 40 56 Z`} fill={p.dressShade} opacity="0.55" />
      {/* collar */}
      <path d="M33 57 L40 63 L47 57 L44 54 L36 54 Z" fill="#fdfbf9" />

      {/* arms */}
      <rect x="15" y="60" width="7" height="20" rx="3.5" fill={p.skin} />
      <rect x="58" y="60" width="7" height="20" rx="3.5" fill={p.skin} />

      {/* hair behind */}
      <ellipse cx="40" cy="33" rx="25" ry="26" fill={p.hair} />
      {/* head */}
      <ellipse cx="40" cy="34" rx="20" ry="21" fill={p.skin} />
      {/* fringe */}
      <path d="M20 29 C 24 16, 56 16, 60 29 C 52 23, 28 23, 20 29 Z" fill={p.hair} />

      {/* eyes */}
      {state === 'done' ? (
        <>
          <path d="M30 35 q4 -4 8 0" stroke="#3c3a40" strokeWidth="1.8" fill="none" strokeLinecap="round" />
          <path d="M42 35 q4 -4 8 0" stroke="#3c3a40" strokeWidth="1.8" fill="none" strokeLinecap="round" />
        </>
      ) : (
        <>
          <ellipse cx="33" cy="35" rx="2.4" ry="2.9" fill="#3c3a40" />
          <ellipse cx="47" cy="35" rx="2.4" ry="2.9" fill="#3c3a40" />
          <circle cx="33.8" cy="34" r="0.8" fill="#fff" />
          <circle cx="47.8" cy="34" r="0.8" fill="#fff" />
        </>
      )}

      {/* brows — lifted while working, level otherwise, drawn down when blocked */}
      <path
        d={
          state === 'working' || state === 'speaking'
            ? 'M29 28 q4 -2.5 8 -0.5 M43 27.5 q4 -2 8 0.5'
            : state === 'blocked'
              ? 'M29 28 q4 2 8 1 M43 29 q4 -1 8 -1'
              : 'M29 29 h8 M43 29 h8'
        }
        stroke="#6b4f3f" strokeWidth="1.4" fill="none" strokeLinecap="round"
      />

      {/* cheeks + mouth */}
      <circle cx="27" cy="41" r="3" fill="#e9a9ad" opacity="0.4" />
      <circle cx="53" cy="41" r="3" fill="#e9a9ad" opacity="0.4" />
      {state === 'speaking' ? (
        <ellipse cx="40" cy="44" rx="3" ry="3.6" fill="#b96b74" />
      ) : state === 'blocked' ? (
        <path d="M36 46 q4 -2.5 8 0" stroke="#b96b74" strokeWidth="1.6" fill="none" strokeLinecap="round" />
      ) : (
        <path d="M36 44 q4 3.5 8 0" stroke="#b96b74" strokeWidth="1.6" fill="none" strokeLinecap="round" />
      )}

      <Prop agent={agent} palette={p} />
    </svg>
  )
}

/** One object per agent, held in the right hand, naming what she is responsible for. */
function Prop({ agent, palette }: { agent: string; palette: DollPalette }) {
  switch (agent) {
    case 'Boss': // a small bell — she is the one who calls it
      return (
        <g>
          <path d="M58 70 q-5 0 -5 7 h10 q0 -7 -5 -7 Z" fill="#e8d7bb" stroke={palette.accent} strokeWidth="1" />
          <rect x="52" y="77" width="12" height="2" rx="1" fill={palette.accent} />
          <circle cx="58" cy="67.5" r="1.6" fill={palette.accent} />
        </g>
      )
    case 'Inventory': // a clipboard
      return (
        <g>
          <rect x="52" y="64" width="13" height="17" rx="1.5" fill="#fdfbf9" stroke={palette.accent} strokeWidth="1" />
          <rect x="56" y="62" width="5" height="3" rx="1" fill={palette.accent} />
          <path d="M55 70 h7 M55 73 h7 M55 76 h4" stroke={palette.accent} strokeWidth="1" strokeLinecap="round" />
        </g>
      )
    case 'Accounting': // a ledger with coins
      return (
        <g>
          <rect x="52" y="66" width="14" height="14" rx="1.5" fill="#fdfbf9" stroke={palette.accent} strokeWidth="1" />
          <path d="M59 66 v14" stroke={palette.accent} strokeWidth="1" />
          <circle cx="55.5" cy="72" r="1.6" fill="#e6cf8f" stroke={palette.accent} strokeWidth="0.6" />
          <circle cx="62.5" cy="75" r="1.6" fill="#e6cf8f" stroke={palette.accent} strokeWidth="0.6" />
        </g>
      )
    case 'Facilities': // a key ring
      return (
        <g>
          <circle cx="58" cy="70" r="4.5" fill="none" stroke={palette.accent} strokeWidth="1.6" />
          <path d="M58 74.5 v6 M58 78 h3.5 M58 80.5 h3" stroke={palette.accent} strokeWidth="1.6" strokeLinecap="round" />
        </g>
      )
    default: // Customer Service — a letter, drafted and never sent
      return (
        <g>
          <rect x="51" y="67" width="16" height="11" rx="1.5" fill="#fdfbf9" stroke={palette.accent} strokeWidth="1" />
          <path d="M51 68 l8 6 l8 -6" fill="none" stroke={palette.accent} strokeWidth="1" strokeLinejoin="round" />
        </g>
      )
  }
}
