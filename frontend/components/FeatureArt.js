import { useEffect, useRef, useState } from 'react'
import styles from '../styles/FeatureArt.module.css'

// Canva-style animated illustrations for the landing page "Core features" (looping, GIF-like).
// Everything is drawn in SVG so it stays sharp and tiny; animations only run while on screen.

const d = (seconds) => ({ '--d': `${seconds}s` })

function Sparkle({ x, y, size = 10, delay = 0 }) {
  const s = size
  return (
    <path
      className={styles.twinkle}
      style={d(delay)}
      d={`M${x} ${y - s} Q${x} ${y} ${x + s} ${y} Q${x} ${y} ${x} ${y + s} Q${x} ${y} ${x - s} ${y} Q${x} ${y} ${x} ${y - s}Z`}
      fill="var(--aj-cta-from)"
    />
  )
}

function Blobs({ variant = 0 }) {
  const shapes = [
    ['M60 70 C 120 10, 260 20, 330 60 S 470 120, 440 200 S 300 300, 180 270 S 10 180, 60 70Z', 'M380 250 c 30 -20 70 -10 70 20 s -40 40 -70 20 z'],
    ['M40 120 C 60 30, 220 10, 320 40 S 480 150, 420 230 S 220 300, 120 260 S 20 210, 40 120Z', 'M70 250 c 20 -25 60 -20 60 5 s -35 35 -60 -5 z'],
  ][variant]
  return (
    <>
      <path d={shapes[0]} fill="var(--aj-accent-soft)" />
      <path d={shapes[1]} fill="rgba(30, 58, 138, 0.08)" className={styles.float} />
    </>
  )
}

function SentimentArt() {
  const rows = [
    { y: 118, chip: 'var(--aj-positive)', sign: '+', delay: 0.3 },
    { y: 166, chip: '#94a3b8', sign: '•', delay: 0.9 },
    { y: 214, chip: 'var(--aj-negative)', sign: '−', delay: 1.5 },
  ]
  // Gauge: centre (382, 214), radius 66, arcs from the left (red) over the top to the right (green).
  return (
    <>
      <Blobs />
      <g className={styles.float}>
        <rect x="36" y="52" width="236" height="206" rx="20" fill="#fff" stroke="var(--aj-indigo)" strokeWidth="3" />
        <path d="M36 72 a20 20 0 0 1 20 -20 h196 a20 20 0 0 1 20 20 v18 h-236z" fill="var(--aj-indigo)" />
        <circle cx="60" cy="72" r="6" fill="var(--aj-cta-from)" />
        <text x="74" y="77" className={styles.label} fill="#fff">HEADLINES</text>
        {rows.map((r) => (
          <g key={r.y}>
            <path className={styles.draw} style={d(r.delay)} d={`M58 ${r.y} H196`} stroke="var(--aj-indigo)" strokeWidth="8" strokeLinecap="round" pathLength="1" />
            <path className={styles.draw} style={d(r.delay + 0.2)} d={`M58 ${r.y + 18} H160`} stroke="#cbd5e1" strokeWidth="6" strokeLinecap="round" pathLength="1" />
            <g className={styles.pop} style={d(r.delay + 0.45)}>
              <circle cx="236" cy={r.y + 8} r="16" fill={r.chip} />
              <text x="236" y={r.y + 14} textAnchor="middle" className={styles.sign} fill="#fff">{r.sign}</text>
            </g>
          </g>
        ))}
      </g>

      <path className={styles.draw} style={d(0.2)} d="M316 214 A66 66 0 0 1 349 156.8" stroke="var(--aj-negative)" strokeWidth="16" strokeLinecap="round" pathLength="1" />
      <path className={styles.draw} style={d(0.4)} d="M356 153 A66 66 0 0 1 408 153" stroke="#94a3b8" strokeWidth="16" strokeLinecap="round" pathLength="1" />
      <path className={styles.draw} style={d(0.6)} d="M415 156.8 A66 66 0 0 1 448 214" stroke="var(--aj-positive)" strokeWidth="16" strokeLinecap="round" pathLength="1" />
      <g className={styles.needle}>
        <path d="M382 214 L382 162" stroke="var(--aj-indigo)" strokeWidth="6" strokeLinecap="round" />
      </g>
      <circle cx="382" cy="214" r="10" fill="var(--aj-indigo)" />
      <g className={styles.pop} style={d(2.2)}>
        <rect x="326" y="236" width="112" height="30" rx="15" fill="#fff" stroke="var(--aj-positive)" strokeWidth="2" />
        <text x="382" y="256" textAnchor="middle" className={styles.label} fill="var(--aj-positive)">FinBERT +0.62</text>
      </g>
      <Sparkle x={318} y={70} size={11} delay={0.4} />
      <Sparkle x={452} y={104} size={8} delay={1.4} />
      <Sparkle x={24} y={40} size={9} delay={2.1} />
    </>
  )
}

function ForecastArt() {
  const candles = [
    { x: 92, top: 170, bottom: 212, wickTop: 160, wickBottom: 222, up: true },
    { x: 126, top: 176, bottom: 204, wickTop: 168, wickBottom: 214, up: false },
    { x: 160, top: 150, bottom: 196, wickTop: 140, wickBottom: 204, up: true },
    { x: 194, top: 160, bottom: 186, wickTop: 150, wickBottom: 196, up: false },
    { x: 228, top: 128, bottom: 176, wickTop: 118, wickBottom: 184, up: true },
    { x: 262, top: 118, bottom: 150, wickTop: 108, wickBottom: 160, up: true },
  ]
  return (
    <>
      <Blobs variant={1} />
      <rect x="34" y="34" width="412" height="232" rx="22" fill="#fff" stroke="var(--aj-border)" strokeWidth="2" />
      <path className={styles.draw} style={d(0)} d="M64 64 V236 H420" stroke="#cbd5e1" strokeWidth="3" strokeLinecap="round" fill="none" pathLength="1" />
      {candles.map((c, i) => (
        <g key={c.x} className={styles.growY} style={d(0.2 + i * 0.15)}>
          <path d={`M${c.x} ${c.wickTop} V${c.wickBottom}`} stroke={c.up ? 'var(--aj-positive)' : 'var(--aj-negative)'} strokeWidth="3" strokeLinecap="round" />
          <rect x={c.x - 10} y={c.top} width="20" height={c.bottom - c.top} rx="4" fill={c.up ? 'var(--aj-positive)' : 'var(--aj-negative)'} />
        </g>
      ))}
      <path className={styles.draw} style={d(1.2)} d="M92 190 L126 186 L160 170 L194 174 L228 146 L262 128" stroke="var(--aj-indigo)" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" fill="none" pathLength="1" />
      <path d="M262 92 V236" stroke="var(--aj-border-strong)" strokeWidth="2" strokeDasharray="4 6" />
      <path className={styles.fadeIn} style={d(1.9)} d="M262 128 L404 66 L404 116 Z" fill="var(--aj-accent-soft)" />
      <path className={`${styles.fadeIn} ${styles.flow}`} style={d(1.9)} d="M262 128 C 300 112, 350 96, 404 88" stroke="var(--aj-cta-to)" strokeWidth="4" strokeDasharray="10 8" strokeLinecap="round" fill="none" />
      <circle className={styles.pulse} style={d(2.3)} cx="404" cy="88" r="16" fill="var(--aj-cta-from)" />
      <circle className={styles.pop} style={d(2.3)} cx="404" cy="88" r="8" fill="var(--aj-cta-to)" />
      <g className={styles.pop} style={d(2.6)}>
        <rect x="300" y="42" width="96" height="30" rx="15" fill="var(--aj-positive)" />
        <text x="348" y="62" textAnchor="middle" className={styles.label} fill="#fff">BUY 81%</text>
      </g>
      <Sparkle x={430} y={60} size={10} delay={2.8} />
      <Sparkle x={52} y={40} size={8} delay={1} />
    </>
  )
}

function BotArt() {
  const rules = [
    { y: 110, text: 'Stop-loss', delay: 0.3 },
    { y: 150, text: 'Take-profit', delay: 0.7 },
    { y: 190, text: 'Max capital', delay: 1.1 },
  ]
  return (
    <>
      <Blobs />
      <g className={styles.spin} style={{ transformOrigin: '420px 60px' }}>
        <circle cx="420" cy="60" r="22" fill="none" stroke="rgba(30,58,138,0.35)" strokeWidth="10" strokeDasharray="6 5" />
      </g>
      <circle cx="420" cy="60" r="10" fill="#fff" />
      <g className={styles.spinRev} style={{ transformOrigin: '452px 92px' }}>
        <circle cx="452" cy="92" r="13" fill="none" stroke="var(--aj-cta-from)" strokeWidth="7" strokeDasharray="4 4" />
      </g>

      {/* Rule checklist */}
      <rect x="24" y="72" width="142" height="160" rx="16" fill="#fff" stroke="var(--aj-border-strong)" strokeWidth="2" />
      <rect x="66" y="64" width="58" height="16" rx="8" fill="var(--aj-indigo)" />
      {rules.map((r) => (
        <g key={r.y}>
          <rect x="40" y={r.y - 12} width="22" height="22" rx="6" fill="var(--aj-accent-soft)" stroke="var(--aj-cta-to)" strokeWidth="2" />
          <path className={styles.draw} style={d(r.delay)} d={`M45 ${r.y} l5 5 l9 -10`} stroke="var(--aj-positive)" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round" fill="none" pathLength="1" />
          <text x="72" y={r.y + 5} className={styles.small} fill="var(--aj-indigo)">{r.text}</text>
        </g>
      ))}

      {/* Robot */}
      <g className={styles.float}>
        <path d="M240 70 V92" stroke="var(--aj-indigo)" strokeWidth="4" strokeLinecap="round" />
        <circle className={styles.blinkBulb} cx="240" cy="64" r="8" fill="var(--aj-cta-from)" />
        <rect x="190" y="92" width="100" height="72" rx="24" fill="#fff" stroke="var(--aj-indigo)" strokeWidth="4" />
        <g className={styles.eyes}>
          <circle cx="220" cy="126" r="8" fill="var(--aj-indigo)" />
          <circle cx="260" cy="126" r="8" fill="var(--aj-indigo)" />
        </g>
        <path d="M226 146 Q240 156 254 146" stroke="var(--aj-cta-to)" strokeWidth="4" strokeLinecap="round" fill="none" />
        <rect x="200" y="170" width="80" height="72" rx="20" fill="var(--aj-indigo)" />
        <path d="M240 186 l18 7 v12 c0 12 -8 20 -18 24 c-10 -4 -18 -12 -18 -24 v-12z" fill="#fff" />
        <path className={styles.draw} style={d(1.5)} d="M232 206 l6 6 l11 -12" stroke="var(--aj-positive)" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" fill="none" pathLength="1" />
        <path d="M200 186 L178 210" stroke="var(--aj-indigo)" strokeWidth="8" strokeLinecap="round" />
        <g className={styles.wave}>
          <path d="M280 186 L302 166" stroke="var(--aj-indigo)" strokeWidth="8" strokeLinecap="round" />
        </g>
      </g>

      {/* Order ticket travelling to the broker */}
      <g className={styles.ticket} style={d(1.8)}>
        <rect x="300" y="150" width="62" height="40" rx="8" fill="#fff" stroke="var(--aj-cta-to)" strokeWidth="2.5" />
        <text x="331" y="167" textAnchor="middle" className={styles.tiny} fill="var(--aj-positive)">BUY</text>
        <path d="M312 178 H350" stroke="#cbd5e1" strokeWidth="4" strokeLinecap="round" />
      </g>
      <g>
        <path d="M398 200 L432 182 L466 200 Z" fill="var(--aj-indigo)" />
        <path d="M404 204 V240 M418 204 V240 M432 204 V240 M446 204 V240 M460 204 V240" stroke="var(--aj-indigo)" strokeWidth="5" strokeLinecap="round" />
        <path d="M396 246 H468" stroke="var(--aj-indigo)" strokeWidth="6" strokeLinecap="round" />
      </g>
      <g className={styles.pop} style={d(3)}>
        <circle cx="432" cy="160" r="16" fill="var(--aj-positive)" />
        <path d="M424 160 l5 5 l10 -10" stroke="#fff" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round" fill="none" />
      </g>
      <Sparkle x={330} y={100} size={9} delay={2} />
      <Sparkle x={40} y={262} size={8} delay={0.8} />
    </>
  )
}

function XaiArt() {
  const bars = [
    { y: 112, w: 112, up: true, delay: 0.4 },
    { y: 150, w: 78, up: false, delay: 0.7 },
    { y: 188, w: 58, up: true, delay: 1 },
    { y: 226, w: 40, up: false, delay: 1.3 },
  ]
  return (
    <>
      <Blobs variant={1} />
      <rect x="36" y="44" width="408" height="222" rx="22" fill="#fff" stroke="var(--aj-border)" strokeWidth="2" />
      <path className={styles.draw} style={d(0)} d="M64 78 H190" stroke="var(--aj-indigo)" strokeWidth="9" strokeLinecap="round" pathLength="1" />
      <path d="M262 96 V244" stroke="var(--aj-border-strong)" strokeWidth="2" strokeDasharray="4 5" />
      {bars.map((b) => (
        <g key={b.y}>
          <rect x="64" y={b.y - 7} width={b.up ? 120 : 96} height="14" rx="7" fill="#e2e8f0" />
          <rect
            className={b.up ? styles.growRight : styles.growLeft}
            style={d(b.delay)}
            x={b.up ? 262 : 262 - b.w}
            y={b.y - 11}
            width={b.w}
            height="22"
            rx="8"
            fill={b.up ? 'var(--aj-positive)' : 'var(--aj-negative)'}
          />
        </g>
      ))}
      <g className={styles.glass}>
        <circle cx="0" cy="0" r="30" fill="rgba(255,255,255,0.35)" stroke="var(--aj-indigo)" strokeWidth="6" />
        <path d="M22 22 L44 44" stroke="var(--aj-indigo)" strokeWidth="9" strokeLinecap="round" />
      </g>
      <g className={styles.bulb}>
        <circle cx="416" cy="36" r="17" fill="var(--aj-cta-from)" />
        <rect x="409" y="52" width="14" height="10" rx="3" fill="var(--aj-indigo)" />
      </g>
      <path className={styles.rays} d="M416 6 V12 M442 16 L437 21 M390 16 L395 21 M450 38 H444 M382 38 H388" stroke="var(--aj-cta-to)" strokeWidth="3.5" strokeLinecap="round" />
      <Sparkle x={30} y={36} size={9} delay={1.2} />
      <Sparkle x={458} y={250} size={8} delay={0.5} />
    </>
  )
}

function ChatArt() {
  return (
    <>
      <Blobs />
      <rect x="118" y="20" width="252" height="262" rx="28" fill="#fff" stroke="var(--aj-indigo)" strokeWidth="4" />
      <rect x="208" y="30" width="72" height="10" rx="5" fill="var(--aj-indigo)" />

      <g className={styles.pop} style={d(0.2)}>
        <rect x="222" y="58" width="130" height="38" rx="16" fill="var(--aj-cta-from)" />
        <text x="287" y="82" textAnchor="middle" className={styles.label} fill="var(--aj-ink)">Why SELL?</text>
      </g>

      <g className={styles.typing} style={d(0.9)}>
        <rect x="136" y="110" width="74" height="34" rx="16" fill="#e2e8f0" />
        <circle className={styles.dot} style={d(0)} cx="156" cy="127" r="5" fill="var(--aj-indigo)" />
        <circle className={styles.dot} style={d(0.15)} cx="173" cy="127" r="5" fill="var(--aj-indigo)" />
        <circle className={styles.dot} style={d(0.3)} cx="190" cy="127" r="5" fill="var(--aj-indigo)" />
      </g>

      <g className={styles.pop} style={d(2.3)}>
        <rect x="136" y="110" width="200" height="92" rx="18" fill="#eef2ff" stroke="rgba(30,58,138,0.25)" strokeWidth="2" />
      </g>
      <path className={styles.draw} style={d(2.6)} d="M154 134 H300" stroke="var(--aj-indigo)" strokeWidth="7" strokeLinecap="round" pathLength="1" />
      <path className={styles.draw} style={d(2.9)} d="M154 156 H316" stroke="rgba(30,58,138,0.45)" strokeWidth="7" strokeLinecap="round" pathLength="1" />
      <path className={styles.draw} style={d(3.2)} d="M154 178 H260" stroke="rgba(30,58,138,0.45)" strokeWidth="7" strokeLinecap="round" pathLength="1" />

      <g className={styles.pop} style={d(3.8)}>
        <rect x="248" y="220" width="104" height="36" rx="16" fill="var(--aj-cta-from)" />
        <text x="300" y="243" textAnchor="middle" className={styles.label} fill="var(--aj-ink)">Got it! 👍</text>
      </g>

      {/* Assistant avatar */}
      <g className={styles.float}>
        <circle cx="70" cy="176" r="40" fill="var(--aj-indigo)" />
        <rect x="44" y="160" width="52" height="34" rx="14" fill="#fff" />
        <g className={styles.eyes}>
          <circle cx="60" cy="176" r="5" fill="var(--aj-indigo)" />
          <circle cx="80" cy="176" r="5" fill="var(--aj-indigo)" />
        </g>
        <path d="M70 136 V124" stroke="var(--aj-indigo)" strokeWidth="4" strokeLinecap="round" />
        <circle className={styles.blinkBulb} cx="70" cy="120" r="6" fill="var(--aj-cta-from)" />
      </g>
      <g className={styles.pop} style={d(1.2)}>
        <circle cx="420" cy="92" r="26" fill="var(--aj-accent-soft)" stroke="var(--aj-cta-to)" strokeWidth="3" />
        <text x="420" y="103" textAnchor="middle" className={styles.question} fill="var(--aj-cta-to)">?</text>
      </g>
      <Sparkle x={430} y={210} size={10} delay={3.9} />
      <Sparkle x={40} y={60} size={8} delay={1.6} />
    </>
  )
}

const ART = {
  sentiment: SentimentArt,
  forecast: ForecastArt,
  bot: BotArt,
  xai: XaiArt,
  chat: ChatArt,
}

export default function FeatureArt({ kind, label }) {
  const ref = useRef(null)
  const [on, setOn] = useState(false)
  const Art = ART[kind]

  useEffect(() => {
    const node = ref.current
    if (!node || typeof IntersectionObserver === 'undefined') {
      setOn(true)
      return undefined
    }
    const io = new IntersectionObserver(([entry]) => setOn(entry.isIntersecting), { threshold: 0.2 })
    io.observe(node)
    return () => io.disconnect()
  }, [])

  if (!Art) return null
  return (
    <svg
      ref={ref}
      viewBox="0 0 480 300"
      className={`${styles.art} ${on ? styles.on : ''}`}
      role="img"
      aria-label={label}
    >
      <Art />
    </svg>
  )
}
