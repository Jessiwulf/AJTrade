import { useEffect, useMemo, useRef, useState } from 'react'
import styles from '../styles/Automated.module.css'
import { formatQuantity, unitWord, usdOptions } from '../lib/format'

// Mirrors decide_strategy_side / _exit_trigger in backend/app/api/trading_bot.py and the DCA
// schedule in backend/app/core/scheduler.py.
export const MIN_AI_CONFIDENCE = 70
export const MEAN_REVERSION_MOVE_PCT = 3
export const NEWS_MOMENTUM_SENTIMENT = 0.3
export const DCA_STRATEGY = 'Dollar-Cost Averaging'

export const DCA_INTERVALS = [
  { hours: 1, label: 'Every hour' },
  { hours: 4, label: 'Every 4 hours' },
  { hours: 24, label: 'Every day' },
  { hours: 168, label: 'Every week' },
  { hours: 720, label: 'Every 30 days' },
]

export const STRATEGY_RULES = {
  'Trend Following': {
    buy: `AI says BUY (${MIN_AI_CONFIDENCE}%+ confidence) and the price is up over the last month`,
    sell: `AI says SELL (${MIN_AI_CONFIDENCE}%+ confidence) and the price is down over the last month`,
  },
  'Mean Reversion': {
    buy: `AI says BUY (${MIN_AI_CONFIDENCE}%+) after the price fell ${MEAN_REVERSION_MOVE_PCT}% or more this month`,
    sell: `AI says SELL (${MIN_AI_CONFIDENCE}%+) after the price rose ${MEAN_REVERSION_MOVE_PCT}% or more this month`,
  },
  'News Momentum': {
    buy: `News sentiment (FinBERT) is +${NEWS_MOMENTUM_SENTIMENT.toFixed(2)} or higher, whatever the AI forecast says`,
    sell: `News sentiment (FinBERT) is −${NEWS_MOMENTUM_SENTIMENT.toFixed(2)} or lower, whatever the AI forecast says`,
  },
  'AI Momentum + Sentiment': {
    buy: `AI says BUY (${MIN_AI_CONFIDENCE}%+) and news sentiment is positive`,
    sell: `AI says SELL (${MIN_AI_CONFIDENCE}%+) and news sentiment is negative`,
  },
  [DCA_STRATEGY]: {
    buy: 'on a fixed schedule (Buy Frequency), spending your Max Capital per Trade each time, whatever the AI says',
    sell: 'never on signals, only when an exit you switched on is hit',
  },
}

const SCENARIOS = [
  { id: 'rally', label: 'Price rallies' },
  { id: 'pullback', label: 'Rises, then pulls back' },
  { id: 'fall', label: 'Price falls' },
]

const CHART_HEIGHT = 220
const PAD = { top: 14, right: 118, bottom: 14, left: 56 }

function round1(value) {
  return Math.round(value * 10) / 10
}

function formatPct(value) {
  const rounded = round1(value)
  return `${rounded > 0 ? '+' : rounded < 0 ? '−' : ''}${Math.abs(rounded).toFixed(1)}%`
}

function formatMoney(value) {
  return Number(value).toLocaleString(undefined, usdOptions(value))
}

function intervalLabel(hours) {
  const match = DCA_INTERVALS.find((item) => item.hours === Number(hours))
  return (match ? match.label : `Every ${hours} hours`).toLowerCase()
}

// 1-2-5 tick spacing so large take-profit targets don't produce dozens of grid lines.
function niceStep(span) {
  const raw = span / 5
  const pow = 10 ** Math.floor(Math.log10(raw))
  return [1, 2, 5, 10].map((m) => m * pow).find((step) => step >= raw)
}

// Same exit rules as the backend: take-profit, stop-loss, then the trailing stop once it is above
// entry. A setting of 0 means that exit is off.
function exitTrigger({ entry, price, trail, sl, tr, tp }) {
  if (tp > 0 && price >= entry * (1 + tp / 100)) return 'Take-Profit'
  if (sl > 0 && price <= entry * (1 - sl / 100)) return 'Stop-Loss'
  if (tr > 0 && trail > entry && price <= trail) return 'Trailing Stop'
  return null
}

// Example price path (in % from entry) that shows off the chosen scenario for these settings.
// Exits that are off still get a sensible example move, so the chart shows the bot holding through it.
function scenarioPath(scenario, { sl, tr, tp }) {
  const drop = (sl > 0 ? sl : 8) + 1
  const wiggle = Math.max(Math.min(sl || 5, tr || 5, tp || 5, 5) * 0.12, 0.05)
  const points = [0]
  const ramp = (from, to, steps) => {
    for (let i = 1; i <= steps; i += 1) {
      const base = from + ((to - from) * i) / steps
      points.push(i === steps ? base : base + (i % 2 ? wiggle : -wiggle))
    }
  }
  if (scenario === 'rally') {
    ramp(0, tp > 0 ? tp + wiggle * 2 : 12, 12)
  } else if (scenario === 'pullback') {
    // Peak high enough for the trailing stop to climb above entry, but below take-profit.
    let peak = tr > 0 ? Math.max(tr * 1.8, tr + 0.6) : 6
    if (tp > 0) peak = Math.min(peak, tp * 0.85)
    ramp(0, peak, 8)
    ramp(peak, -drop, 12)
  } else {
    const bump = tr > 0 ? tr * 0.5 : 0.5
    ramp(0, bump, 3)
    ramp(bump, -drop, 10)
  }
  return points
}

function simulate(path, { sl, tr, tp }) {
  const rows = []
  let trail = tr > 0 ? 1 - tr / 100 : 0
  for (let i = 0; i < path.length; i += 1) {
    const price = 1 + path[i] / 100
    if (tr > 0) trail = Math.max(trail, price * (1 - tr / 100))
    const exit = i > 0 ? exitTrigger({ entry: 1, price, trail, sl, tr, tp }) : null
    // The example fills exactly at the level that was crossed, so a coarse example step can't
    // make e.g. a trailing stop look like it sold at a loss.
    const levels = { 'Take-Profit': tp, 'Stop-Loss': -sl, 'Trailing Stop': (trail - 1) * 100 }
    rows.push({ step: i, pct: exit ? levels[exit] : path[i], trailPct: (trail - 1) * 100, trailActive: tr > 0 && trail > 1, exit })
    if (exit) break
  }
  return rows
}

function useElementWidth(fallback) {
  const ref = useRef(null)
  const [width, setWidth] = useState(fallback)
  useEffect(() => {
    const el = ref.current
    if (!el || typeof ResizeObserver === 'undefined') return undefined
    const observer = new ResizeObserver(([entry]) => {
      const next = Math.round(entry.contentRect.width)
      if (next > 0) setWidth(next)
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])
  return [ref, width]
}

function Collapsible({ title, meta, children, defaultOpen = false }) {
  return (
    <details className={styles.collapse} open={defaultOpen}>
      <summary className={styles.collapseSummary}>
        <span className={styles.collapseChevron} aria-hidden="true" />
        <span className={styles.collapseTitle}>{title}</span>
        {meta ? <span className={styles.collapseMeta}>{meta}</span> : null}
      </summary>
      <div className={styles.collapseBody}>{children}</div>
    </details>
  )
}

function PositionGauge({ asset, position, rule, active, unsaved }) {
  const sl = Number(rule.stopLossPct) || 0
  const tr = Number(rule.trailingStopLossPct) || 0
  const tp = Number(rule.takeProfitPct) || 0
  const entry = Number(position.entryPrice)
  const current = Number(position.currentPrice) || entry
  const trail = Number(position.trailingStopLevel) || 0
  const slPrice = entry * (1 - sl / 100)
  const tpPrice = entry * (1 + tp / 100)
  const trigger = exitTrigger({ entry, price: current, trail, sl, tr, tp })
  const trailActive = tr > 0 && trail > entry

  // The track spans stop-loss .. take-profit. When an exit is off, that side stretches to include
  // the current price instead; otherwise a price past the exit is pinned to the edge.
  const loBase = sl > 0 ? slPrice : Math.min(entry * 0.9, current)
  const hiBase = tp > 0 ? tpPrice : Math.max(entry * 1.1, current)
  const lo = loBase - (hiBase - loBase) * 0.06
  const hi = hiBase + (hiBase - loBase) * 0.06
  const pos = (price) => Math.min(Math.max(((price - lo) / (hi - lo)) * 100, 0), 100)
  const offTrack = current > hi ? 'right' : current < lo ? 'left' : null
  const changePct = entry > 0 ? ((current - entry) / entry) * 100 : 0

  const labels = [
    sl > 0 ? { key: 'sl', at: pos(slPrice), text: '▼ Stop-loss', price: slPrice } : null,
    { key: 'entry', at: pos(entry), text: 'Entry', price: entry },
    tp > 0 ? { key: 'tp', at: pos(tpPrice), text: '▲ Take-profit', price: tpPrice } : null,
  ].filter(Boolean)

  const levels = [
    `stop-loss ${sl > 0 ? formatMoney(slPrice) : 'off'}`,
    `take-profit ${tp > 0 ? formatMoney(tpPrice) : 'off'}`,
  ].join(', ')
  let tone = 'hold'
  let message = sl > 0 || tp > 0 || tr > 0
    ? `Holding: no exit reached (${levels}).`
    : 'Holding long-term: all exits are off, so the bot never sells this position on its own.'
  if (trigger && active) {
    tone = 'sell'
    message = `${trigger} reached. The bot sells its ${asset} ${unitWord(asset)} on its next check (within about a minute).`
  } else if (trigger) {
    tone = 'warn'
    message = `${trigger} reached, but the bot is off for ${asset}, so it will not sell. Turn the bot on and save to let it close the position.`
  }

  return (
    <div className={styles.gaugeCard}>
      <div>
        <div className={styles.gaugeTitle}>Your open {asset} bot position</div>
        <div className={styles.gaugeSub}>
          {formatQuantity(position.quantity || 0)} {unitWord(asset)} · entry {formatMoney(entry)} · now {formatMoney(current)}{' '}
          <span className={changePct >= 0 ? styles.pos : styles.neg}>({formatPct(changePct)})</span>
        </div>
      </div>

      <div className={styles.gauge} role="img" aria-label={`${asset} at ${formatMoney(current)}. Entry ${formatMoney(entry)}, ${levels}.`}>
        {sl > 0 ? <div className={styles.gaugeLoss} style={{ left: `${pos(slPrice)}%`, width: `${pos(entry) - pos(slPrice)}%` }} /> : null}
        {tp > 0 ? <div className={styles.gaugeGain} style={{ left: `${pos(entry)}%`, width: `${pos(tpPrice) - pos(entry)}%` }} /> : null}
        {sl > 0 ? <span className={`${styles.gaugeTick} ${styles.gaugeTickSl}`} style={{ left: `${pos(slPrice)}%` }} /> : null}
        <span className={`${styles.gaugeTick} ${styles.gaugeTickEntry}`} style={{ left: `${pos(entry)}%` }} />
        {tp > 0 ? <span className={`${styles.gaugeTick} ${styles.gaugeTickTp}`} style={{ left: `${pos(tpPrice)}%` }} /> : null}
        {trailActive ? <span className={`${styles.gaugeTick} ${styles.gaugeTickTrail}`} style={{ left: `${pos(trail)}%` }} /> : null}
        <span className={styles.gaugeNow} style={{ left: `${pos(current)}%` }}>
          {/* Near an edge, anchor the label inward so it stays inside the card. */}
          <span
            className={`${styles.gaugeNowLabel} ${
              pos(current) > 85 ? styles.gaugeNowLabelEnd : pos(current) < 15 ? styles.gaugeNowLabelStart : ''
            }`}
          >
            {offTrack === 'right' ? 'Now ▶ ' : offTrack === 'left' ? '◀ Now ' : 'Now '}
            {formatMoney(current)}
          </span>
        </span>
      </div>
      <div className={styles.gaugeLabels}>
        {labels.map((label) => (
          <span
            key={label.key}
            className={label.at < 12 ? styles.gaugeLabelStart : label.at > 88 ? styles.gaugeLabelEnd : ''}
            style={{ left: `${label.at}%` }}
          >
            {label.text}<br />{formatMoney(label.price)}
          </span>
        ))}
      </div>

      <p className={`${styles.gaugeMessage} ${styles[`gaugeMessage_${tone}`] || ''}`}>
        {message}
        {trailActive && !trigger ? ` Trailing stop is locked in at ${formatMoney(trail)}.` : ''}
        {unsaved ? ' (Shown with your unsaved settings; the bot uses the saved ones.)' : ''}
      </p>
    </div>
  )
}

function ExampleChart({ sl, tr, tp }) {
  const [scenario, setScenario] = useState('pullback')
  const [hoverIdx, setHoverIdx] = useState(null)
  const [wrapRef, width] = useElementWidth(640)
  const W = Math.max(width, 320)
  const H = CHART_HEIGHT

  const rows = useMemo(() => simulate(scenarioPath(scenario, { sl, tr, tp }), { sl, tr, tp }), [scenario, sl, tr, tp])
  const exitRow = rows[rows.length - 1]?.exit ? rows[rows.length - 1] : null

  const dataMax = Math.max(tp > 0 ? tp : 0, ...rows.map((r) => r.pct))
  const dataMin = Math.min(sl > 0 ? -sl : 0, ...rows.map((r) => r.pct))
  const pad = Math.max((dataMax - dataMin) * 0.08, 0.3)
  const yMax = dataMax + pad
  const yMin = dataMin - pad
  const steps = Math.max(rows.length - 1, 1)
  const plotRight = W - PAD.right
  const x = (step) => PAD.left + (step / steps) * (plotRight - PAD.left)
  const y = (pct) => PAD.top + ((yMax - pct) / (yMax - yMin)) * (H - PAD.top - PAD.bottom)

  const pricePath = rows.map((r, i) => `${i ? 'L' : 'M'}${x(r.step).toFixed(1)},${y(r.pct).toFixed(1)}`).join(' ')
  const trailPath = rows
    .filter((r) => r.trailActive)
    .map((r, i) => `${i ? 'L' : 'M'}${x(r.step).toFixed(1)},${y(r.trailPct).toFixed(1)}`)
    .join(' ')
  const gridTicks = useMemo(() => {
    const step = niceStep(yMax - yMin)
    const ticks = []
    for (let t = Math.ceil(yMin / step) * step; t <= yMax; t += step) ticks.push(t)
    return ticks
  }, [yMax, yMin])

  const priceAt = (pct) => 100 * (1 + pct / 100)
  const hovered = hoverIdx != null ? rows[hoverIdx] : null
  const outcome = exitRow
    ? `SELL by ${exitRow.exit} at ${formatPct(exitRow.pct)} (${formatMoney(priceAt(exitRow.pct))})`
    : 'Still holding at the end of this example'

  function handleMove(event) {
    const box = event.currentTarget.getBoundingClientRect()
    const step = Math.round(((event.clientX - box.left - PAD.left) / (plotRight - PAD.left)) * steps)
    setHoverIdx(step >= 0 && step < rows.length ? step : null)
  }

  return (
    <>
      <div className={styles.chartToolbar}>
        <div className={styles.chartLegend} aria-hidden="true">
          <span><i className={`${styles.legendSwatch} ${styles.swPrice}`} />Price</span>
          {tr > 0 ? <span><i className={`${styles.legendSwatch} ${styles.swTrail}`} />Trailing stop</span> : null}
          <span><i className={`${styles.legendDot} ${styles.dotBuy}`} />BUY</span>
          <span><i className={`${styles.legendDot} ${styles.dotSell}`} />SELL</span>
        </div>
        <div className={styles.scenarioRow} role="group" aria-label="Example scenario">
          {SCENARIOS.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`${styles.scenarioBtn} ${scenario === item.id ? styles.scenarioBtnActive : ''}`}
              onClick={() => setScenario(item.id)}
              aria-pressed={scenario === item.id}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      <div className={styles.chartWrap} ref={wrapRef}>
        <svg
          width={W}
          height={H}
          viewBox={`0 0 ${W} ${H}`}
          className={styles.chartSvg}
          role="img"
          aria-label={`Example trade bought at $100. Take-profit ${tp > 0 ? formatPct(tp) : 'off'}, stop-loss ${sl > 0 ? formatPct(-sl) : 'off'}, trailing stop ${tr > 0 ? `${tr}% below the high` : 'off'}. Outcome: ${outcome}.`}
          onMouseMove={handleMove}
          onMouseLeave={() => setHoverIdx(null)}
        >
          {gridTicks.map((t) => (
            <g key={t}>
              <line x1={PAD.left} x2={plotRight} y1={y(t)} y2={y(t)} className={styles.chartGrid} />
              <text x={PAD.left - 8} y={y(t) + 4} textAnchor="end" className={styles.chartAxis}>{formatPct(t)}</text>
            </g>
          ))}

          {tp > 0 ? <line x1={PAD.left} x2={plotRight} y1={y(tp)} y2={y(tp)} className={styles.chartTp} /> : null}
          <line x1={PAD.left} x2={plotRight} y1={y(0)} y2={y(0)} className={styles.chartEntry} />
          {sl > 0 ? <line x1={PAD.left} x2={plotRight} y1={y(-sl)} y2={y(-sl)} className={styles.chartSl} /> : null}
          {tp > 0 ? <text x={plotRight + 8} y={y(tp) + 4} className={styles.chartLabel}>▲ Take-profit {formatPct(tp)}</text> : null}
          <text x={plotRight + 8} y={y(0) + 4} className={styles.chartAxis}>Entry $100</text>
          {sl > 0 ? <text x={plotRight + 8} y={y(-sl) + 4} className={styles.chartLabel}>▼ Stop-loss {formatPct(-sl)}</text> : null}

          {trailPath ? <path d={trailPath} className={styles.chartTrail} /> : null}
          <path d={pricePath} className={styles.chartPrice} />
          <circle cx={x(0)} cy={y(0)} r={5} className={styles.chartBuyDot} />
          {exitRow ? <circle cx={x(exitRow.step)} cy={y(exitRow.pct)} r={6} className={styles.chartSellDot} /> : null}

          {hovered ? (
            <g pointerEvents="none">
              <line x1={x(hovered.step)} x2={x(hovered.step)} y1={PAD.top} y2={H - PAD.bottom} className={styles.chartCrosshair} />
              <circle cx={x(hovered.step)} cy={y(hovered.pct)} r={4} className={styles.chartHoverDot} />
            </g>
          ) : null}
        </svg>

        {hovered ? (
          <div className={styles.chartTooltip} style={{ left: Math.min(Math.max(x(hovered.step), 90), W - 90) }} role="status">
            <strong>{hovered.step === 0 ? 'BUY' : hovered.exit ? `SELL · ${hovered.exit}` : 'HOLD'}</strong>
            <span>Price {formatMoney(priceAt(hovered.pct))} ({formatPct(hovered.pct)})</span>
            <span>
              {tr <= 0
                ? 'Trailing stop off'
                : hovered.trailActive
                  ? `Trailing stop ${formatMoney(priceAt(hovered.trailPct))}`
                  : 'Trailing stop not active yet'}
            </span>
          </div>
        ) : null}
      </div>

      <p className={styles.previewOutcome}>
        <span className={`${styles.outcomeDot} ${exitRow ? styles.dotSell : styles.dotHold}`} aria-hidden="true" />
        <strong>Outcome:</strong> {outcome}
      </p>

      <details className={styles.previewTable}>
        <summary>View example as table</summary>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Step</th>
              <th>Price</th>
              <th>Change</th>
              <th>Trailing stop</th>
              <th>Bot action</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.step}>
                <td>{row.step}</td>
                <td>{formatMoney(priceAt(row.pct))}</td>
                <td>{formatPct(row.pct)}</td>
                <td>{row.trailActive ? formatMoney(priceAt(row.trailPct)) : '—'}</td>
                <td>{row.step === 0 ? 'BUY' : row.exit ? `SELL (${row.exit})` : 'HOLD'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </>
  )
}

function ExitTile({ kind, label, value, onText, offText }) {
  const on = Number(value) > 0
  return (
    <div className={`${styles.exitTile} ${styles[kind]} ${on ? '' : styles.exitTileOff}`}>
      <span className={styles.exitTileLabel}>{label}</span>
      <strong>{on ? onText.value : 'Off'}</strong>
      <span>{on ? onText.detail : offText}</span>
    </div>
  )
}

export default function BotBehaviorPreview({ asset, rule, active, unsaved, position }) {
  const sl = Number(rule.stopLossPct) || 0
  const tr = Number(rule.trailingStopLossPct) || 0
  const tp = Number(rule.takeProfitPct) || 0
  const anyExit = sl > 0 || tr > 0 || tp > 0
  const hasAsset = asset && asset !== 'NO_ASSET'
  const assetName = hasAsset ? asset : 'this asset'
  const strategy = STRATEGY_RULES[rule.strategy] ? rule.strategy : 'Trend Following'
  const isDca = strategy === DCA_STRATEGY
  const dcaAmount = formatMoney(Number(rule.maxCapitalPerTrade) || 0)
  const dcaEvery = intervalLabel(rule.dcaIntervalHours || 24)

  const buyText = isDca
    ? `${dcaEvery}: ${dcaAmount} of ${assetName}, whatever the AI says.`
    : `when ${STRATEGY_RULES[strategy].buy}.`
  const sellText = isDca
    ? anyExit
      ? 'never on signals; only when an exit you switched on is hit.'
      : 'never: all exits are off, so the position just keeps growing.'
    : `when ${STRATEGY_RULES[strategy].sell}${anyExit ? ', or when an open position hits one of the exits' : ''}.`

  let statusText = `It will not buy or sell ${assetName}. Turn the bot on in step 1 and save to start.`
  if (active && isDca) {
    statusText = `Buys ${dcaAmount} of ${assetName} ${dcaEvery}${anyExit ? ' and closes the position at the exits below.' : '. All exits are off, so it holds long-term.'}`
  } else if (active) {
    statusText = `Checks every minute: buys and sells ${assetName} by the ${strategy} rules${anyExit ? ' and closes its position at the exits below.' : '. All exits are off.'}`
  }

  return (
    <div className={styles.preview} aria-label="Bot behavior preview">
      <div className={`${styles.previewStatus} ${active ? styles.previewStatusOn : styles.previewStatusOff}`}>
        <span className={styles.statusDot} aria-hidden="true" />
        <div className={styles.previewStatusBody}>
          <div className={styles.previewStatusTitle}>
            {!hasAsset ? 'No asset selected' : active ? `Bot is running on ${asset}` : `Bot is stopped for ${asset}`}
          </div>
          <div className={styles.previewStatusText}>{statusText}</div>
        </div>
        {unsaved ? <span className={styles.unsavedBadge}>Unsaved changes</span> : null}
      </div>

      {position?.entryPrice > 0 ? (
        <PositionGauge asset={assetName} position={position} rule={rule} active={active} unsaved={unsaved} />
      ) : null}

      <Collapsible title="When the bot trades" meta={isDca ? `${strategy} · ${dcaEvery}` : strategy}>
        <div className={styles.previewRules}>
          <div className={`${styles.ruleRow} ${styles.ruleBuy}`}>
            <span className={styles.ruleTag}>▲ BUY</span>
            <span>{buyText}</span>
          </div>
          <div className={`${styles.ruleRow} ${styles.ruleSell}`}>
            <span className={styles.ruleTag}>▼ SELL</span>
            <span>{sellText} Only sells shares the bot bought; your manual buys are never sold.</span>
          </div>
          <div className={`${styles.ruleRow} ${styles.ruleHold}`}>
            <span className={styles.ruleTag}>■ HOLD</span>
            <span>{isDca ? 'between scheduled buys.' : 'otherwise — no trade.'}</span>
          </div>
        </div>
      </Collapsible>

      <Collapsible
        title="When the bot sells after a BUY"
        meta={`TP ${tp > 0 ? formatPct(tp) : 'off'} · Trail ${tr > 0 ? `${tr.toFixed(1)}%` : 'off'} · SL ${sl > 0 ? formatPct(-sl) : 'off'}`}
      >
        <div className={styles.exitTiles}>
          <ExitTile
            kind="exitTileTp"
            label="▲ Take-profit"
            value={tp}
            onText={{ value: formatPct(tp), detail: `Sells once the price is ${tp.toFixed(1)}% above entry.` }}
            offText="No profit target: holds however far the price rises."
          />
          <ExitTile
            kind="exitTileTrail"
            label="Trailing stop"
            value={tr}
            onText={{ value: `${tr.toFixed(1)}% below high`, detail: 'Follows the highest price; takes over once it is above entry, locking in gains.' }}
            offText="No trailing stop: gains are not locked in automatically."
          />
          <ExitTile
            kind="exitTileSl"
            label="▼ Stop-loss"
            value={sl}
            onText={{ value: formatPct(-sl), detail: `Sells if the price falls ${sl.toFixed(1)}% below entry.` }}
            offText="No loss limit: holds through drops."
          />
        </div>
        <ExampleChart sl={sl} tr={tr} tp={tp} />
        <p className={styles.previewFootnote}>
          {anyExit
            ? 'Exits run only while the bot is on. The bot checks every minute and sells at the market price, which can be a little past the level when the price moves fast.'
            : 'All exits are off: the bot never sells on its own. Suits long-term saving such as dollar-cost averaging.'}
        </p>
      </Collapsible>
    </div>
  )
}
