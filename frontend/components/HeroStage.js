import { useEffect, useRef, useState } from 'react'
import styles from '../styles/HeroStage.module.css'

// Illustrative examples in the same design as the AI Insights cards. Cycles every few seconds; the
// visitor can pick an asset, hover the price chart, and tilt the whole stage with the pointer.
const SCENARIOS = [
  {
    symbol: 'SOL-USD',
    price: 118.94,
    change: 12.6,
    signal: 'SELL',
    confidence: 78,
    probUp: 22,
    closes: [103, 101, 101.5, 104, 102.8, 103.6, 105.9, 104, 103.7, 102.2, 99.9, 102.4, 102, 100.2, 102.4, 99.2, 96.9, 98.4, 101, 111.4, 110.5, 110.6, 116.9, 116.8, 114.7, 116.3, 120.8, 120.4, 122.1, 118.9],
    drivers: [
      { label: 'Intraday range', value: -28.2 },
      { label: '10-day average', value: -16.5 },
      { label: '3-day return', value: 11.9 },
    ],
    news: { headline: 'Solana network activity hits a six-month high', label: 'Bullish', score: 0.32 },
    plan: { strategy: 'Mean Reversion', sl: 6.7, trail: 5, tp: 13.4 },
  },
  {
    symbol: 'ETH-USD',
    price: 2682.68,
    change: 4.1,
    signal: 'BUY',
    confidence: 81,
    probUp: 81,
    closes: [2555, 2540, 2562, 2590, 2575, 2548, 2566, 2601, 2622, 2610, 2585, 2598, 2630, 2618, 2604, 2627, 2651, 2640, 2622, 2638, 2660, 2655, 2671, 2648, 2663, 2690, 2702, 2685, 2677, 2683],
    drivers: [
      { label: 'News sentiment', value: 24.6 },
      { label: 'Daily return', value: 18.3 },
      { label: 'Volume change', value: -9.4 },
    ],
    news: { headline: 'Ethereum ETF inflows extend a weekly streak', label: 'Strong bullish', score: 0.51 },
    plan: { strategy: 'Trend Following', sl: 5.4, trail: 4.1, tp: 10.8 },
  },
  {
    symbol: 'AAPL',
    price: 229.4,
    change: -1.8,
    signal: 'HOLD',
    confidence: 53,
    probUp: 47,
    closes: [233.5, 234.1, 232.8, 231.9, 233.2, 234.6, 233.9, 232.4, 231.1, 231.8, 230.6, 229.9, 231.2, 232.5, 231.7, 230.4, 229.8, 230.9, 231.6, 230.2, 229.1, 228.6, 229.8, 230.7, 229.9, 228.8, 229.5, 230.1, 229.2, 229.4],
    drivers: [
      { label: '5-day average', value: 12.2 },
      { label: 'Intraday range', value: -11.7 },
      { label: 'News sentiment', value: 6.1 },
    ],
    news: { headline: 'Apple suppliers report steady iPhone orders', label: 'Neutral', score: 0.04 },
    plan: { strategy: 'Trend Following', sl: 3.1, trail: 2.3, tp: 6.2 },
  },
]

const CYCLE_MS = 5200
const SPARK_W = 300
const SPARK_H = 84

function money(value) {
  return value.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function useCountUp(target, duration = 700) {
  const [value, setValue] = useState(target)
  const fromRef = useRef(target)
  useEffect(() => {
    const from = fromRef.current
    if (from === target) return undefined
    const start = performance.now()
    let frame
    const tick = (now) => {
      const t = Math.min(1, (now - start) / duration)
      setValue(Math.round(from + (target - from) * (1 - (1 - t) ** 3)))
      if (t < 1) frame = requestAnimationFrame(tick)
      else fromRef.current = target
    }
    frame = requestAnimationFrame(tick)
    return () => {
      cancelAnimationFrame(frame)
      fromRef.current = target
    }
  }, [target, duration])
  return value
}

function toneOf(signal) {
  return signal === 'BUY' ? styles.buy : signal === 'SELL' ? styles.sell : styles.hold
}

function Sparkline({ closes, symbol }) {
  const [active, setActive] = useState(null)
  const last = closes.length - 1
  const min = Math.min(...closes)
  const span = Math.max(...closes) - min || 1
  const pts = closes.map((v, i) => [(i / last) * SPARK_W, SPARK_H - ((v - min) / span) * (SPARK_H - 10) - 5])
  const line = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const up = closes[last] >= closes[0]

  function pick(event) {
    const rect = event.currentTarget.getBoundingClientRect()
    setActive(Math.min(last, Math.max(0, Math.round(((event.clientX - rect.left) / rect.width) * last))))
  }

  const x = active != null ? (active / last) * 100 : 0
  const y = active != null ? (pts[active][1] / SPARK_H) * 100 : 0
  return (
    <div className={styles.sparkPlot} onPointerMove={pick} onPointerLeave={() => setActive(null)}>
      <svg key={symbol} viewBox={`0 0 ${SPARK_W} ${SPARK_H}`} preserveAspectRatio="none" className={styles.sparkSvg} aria-hidden="true">
        <path d={`${line} L${SPARK_W},${SPARK_H} L0,${SPARK_H} Z`} className={up ? styles.areaUp : styles.areaDown} />
        <path d={line} className={up ? styles.lineUp : styles.lineDown} vectorEffect="non-scaling-stroke" pathLength="1" />
      </svg>
      {active != null ? (
        <>
          <span className={styles.cursor} style={{ left: `${x}%` }} />
          <span className={styles.dot} style={{ left: `${x}%`, top: `${y}%` }} />
          <span className={`${styles.tip} ${x > 55 ? styles.tipFlip : ''}`} style={{ left: `${x}%` }}>
            Day {active + 1} · <strong>{money(closes[active])}</strong>
          </span>
        </>
      ) : null}
    </div>
  )
}

export default function HeroStage() {
  const [index, setIndex] = useState(0)
  const [hovering, setHovering] = useState(false)
  const stageRef = useRef(null)
  const innerRef = useRef(null)
  const pointer = useRef(null)
  const s = SCENARIOS[index]
  const confidence = useCountUp(s.confidence)
  const maxDriver = Math.max(...s.drivers.map((d) => Math.abs(d.value)))

  // Auto-cycle through the examples unless the visitor is interacting with the stage.
  useEffect(() => {
    if (hovering) return undefined
    const id = setTimeout(() => setIndex((i) => (i + 1) % SCENARIOS.length), CYCLE_MS)
    return () => clearTimeout(id)
  }, [index, hovering])

  // 3D tilt: follows the pointer; otherwise sways gently (the idle sway is skipped with reduced motion,
  // pointer tilt is kept because the visitor drives it). Eased every frame, paused off-screen.
  useEffect(() => {
    const stage = stageRef.current
    const inner = innerRef.current
    if (!stage || !inner) return undefined
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    const narrow = window.matchMedia?.('(max-width: 980px)')
    let frame
    let visible = true
    const current = { rx: 4, ry: -8, gx: 50, gy: 30 }
    const start = performance.now()
    const tick = (now) => {
      if (visible && !narrow?.matches) {
        const t = (now - start) / 1000
        const target = pointer.current
          ? { rx: -pointer.current.ny * 6, ry: pointer.current.nx * 14, gx: 50 + pointer.current.nx * 40, gy: 30 + pointer.current.ny * 40 }
          : reduce
            ? { rx: 4, ry: -8, gx: 50, gy: 30 }
            : { rx: Math.sin(t * 0.6) * 3 + 4, ry: Math.cos(t * 0.45) * 7 - 8, gx: 50, gy: 30 }
        for (const k of Object.keys(current)) current[k] += (target[k] - current[k]) * 0.08
        inner.style.setProperty('--rx', `${current.rx.toFixed(2)}deg`)
        inner.style.setProperty('--ry', `${current.ry.toFixed(2)}deg`)
        inner.style.setProperty('--gx', `${current.gx.toFixed(1)}%`)
        inner.style.setProperty('--gy', `${current.gy.toFixed(1)}%`)
      }
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    const io = typeof IntersectionObserver !== 'undefined'
      ? new IntersectionObserver(([entry]) => { visible = entry.isIntersecting })
      : null
    io?.observe(stage)
    return () => {
      cancelAnimationFrame(frame)
      io?.disconnect()
    }
  }, [])

  function onPointerMove(event) {
    const rect = stageRef.current.getBoundingClientRect()
    pointer.current = {
      nx: ((event.clientX - rect.left) / rect.width) * 2 - 1,
      ny: ((event.clientY - rect.top) / rect.height) * 2 - 1,
    }
  }

  function onPointerLeave() {
    pointer.current = null
    setHovering(false)
  }

  return (
    <div className={styles.wrap}>
      <div
        ref={stageRef}
        className={styles.stage}
        onPointerMove={onPointerMove}
        onPointerEnter={() => setHovering(true)}
        onPointerLeave={onPointerLeave}
        aria-label={`Example AI insight for ${s.symbol}: ${s.confidence}% ${s.signal}`}
        role="img"
      >
        <div ref={innerRef} className={styles.inner}>
          <div className={styles.floor} aria-hidden="true" />

          {/* Price chart (back layer) */}
          <div className={`${styles.layer} ${styles.layerPrice}`}>
            <div className={styles.card}>
              <p className={styles.label}>Price · last 30 days</p>
              <div className={styles.priceHead}>
                <strong>{s.symbol}</strong>
                <span>{money(s.price)}</span>
                <span className={`${styles.chip} ${s.change >= 0 ? styles.chipUp : styles.chipDown}`}>
                  {s.change >= 0 ? '+' : '−'}{Math.abs(s.change).toFixed(1)}% 1M
                </span>
              </div>
              <Sparkline closes={s.closes} symbol={s.symbol} />
            </div>
          </div>

          {/* SHAP drivers */}
          <div className={`${styles.layer} ${styles.layerDrivers}`}>
            <div className={styles.card}>
              <p className={styles.label}>Why the model says {s.signal}</p>
              <ul className={styles.drivers}>
                {s.drivers.map((d, i) => {
                  const width = `${(Math.abs(d.value) / maxDriver) * 50}%`
                  return (
                    <li key={`${s.symbol}-${d.label}`}>
                      <span className={styles.driverLabel}>{d.label}</span>
                      <span className={styles.track}>
                        <span className={styles.mid} />
                        <span
                          className={d.value >= 0 ? styles.barUp : styles.barDown}
                          style={{ ...(d.value >= 0 ? { left: '50%' } : { right: '50%' }), '--w': width, '--d': `${0.1 + i * 0.1}s` }}
                        />
                      </span>
                      <span className={`${styles.driverValue} ${d.value >= 0 ? styles.buy : styles.sell}`}>
                        {d.value >= 0 ? '▲' : '▼'} {Math.abs(d.value).toFixed(1)}%
                      </span>
                    </li>
                  )
                })}
              </ul>
            </div>
          </div>

          {/* News sentiment */}
          <div className={`${styles.layer} ${styles.layerNews}`}>
            <div key={s.symbol} className={`${styles.card} ${styles.swap}`}>
              <p className={styles.label}>News · FinBERT</p>
              <p className={styles.headline}>{s.news.headline}</p>
              <span className={styles.newsChip}>
                {s.news.label} <strong>{s.news.score >= 0 ? '+' : '−'}{Math.abs(s.news.score).toFixed(2)}</strong>
              </span>
            </div>
          </div>

          {/* AI signal (front layer) */}
          <div className={`${styles.layer} ${styles.layerSignal}`}>
            <div className={`${styles.card} ${styles.signalCard} ${s.signal === 'BUY' ? styles.signalBuy : s.signal === 'SELL' ? styles.signalSell : ''}`}>
              <span className={styles.glare} aria-hidden="true" />
              <p className={styles.label}>AI signal</p>
              <p className={styles.score}>
                <span className={styles.scoreValue}>{confidence}%</span>
                <span key={s.signal + s.symbol} className={`${styles.scoreSignal} ${styles.swap} ${toneOf(s.signal)}`}>{s.signal}</span>
              </p>
              <p className={styles.scoreText}>
                <strong>{s.probUp}%</strong> chance it closes higher next day
              </p>
              <div className={styles.gauge}>
                <span className={styles.gaugeSell} />
                <span className={styles.gaugeBuy} />
                <span className={styles.gaugeMarker} style={{ left: `${s.probUp}%` }} />
              </div>
              <div className={styles.gaugeScale}>
                <span>SELL</span>
                <span>HOLD</span>
                <span>BUY</span>
              </div>
            </div>
          </div>

          {/* Automate plan */}
          <div className={`${styles.layer} ${styles.layerPlan}`}>
            <div className={`${styles.card} ${styles.planCard}`}>
              <div>
                <p className={styles.label}>Automate sets</p>
                <strong key={s.symbol} className={`${styles.planStrategy} ${styles.swap}`}>{s.plan.strategy}</strong>
              </div>
              <div className={styles.tiles}>
                <span><small>Stop-loss</small>{s.plan.sl}%</span>
                <span><small>Trailing</small>{s.plan.trail}%</span>
                <span><small>Take-profit</small>{s.plan.tp}%</span>
              </div>
              <span className={styles.planButton}>Automate →</span>
            </div>
          </div>
        </div>
      </div>

      <div className={styles.switcher} role="tablist" aria-label="Example assets">
        {SCENARIOS.map((item, i) => (
          <button
            key={item.symbol}
            type="button"
            role="tab"
            aria-selected={i === index}
            className={`${styles.switch} ${i === index ? styles.switchOn : ''}`}
            onClick={() => setIndex(i)}
          >
            {item.symbol}
            <span className={`${styles.switchSignal} ${toneOf(item.signal)}`}>{item.signal}</span>
            {i === index && !hovering ? <span key={index} className={styles.switchProgress} /> : null}
          </button>
        ))}
      </div>
    </div>
  )
}
