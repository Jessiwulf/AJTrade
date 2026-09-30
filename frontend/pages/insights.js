import { useState } from 'react'
import useSWR from 'swr'
import Link from 'next/link'
import { useRouter } from 'next/router'
import AppShell from '../components/AppShell'
import InfoTip from '../components/InfoTip'
import { STRATEGY_RULES } from '../components/BotBehaviorPreview'
import { storeAutoConfig, suggestBotConfig } from '../lib/autoConfig'
import { unitWord, usdOptions } from '../lib/format'
import styles from '../styles/Insights.module.css'

// The bot only acts on AI signals at or above this confidence (see backend BOT_MIN_SIGNAL_CONFIDENCE).
const BOT_MIN_CONFIDENCE = 70

const TIPS = {
  page: (
    <>
      <span><strong>How AI insights work.</strong> Every few minutes each watchlist asset is re-analysed:</span>
      <span>1. A <strong>LightGBM</strong> model is trained on the asset&apos;s last ~180 trading days of prices plus <strong>FinBERT</strong> news sentiment.</span>
      <span>2. It predicts the probability that the <strong>next trading day closes higher</strong> than today (&quot;up probability&quot;).</span>
      <span>3. <strong>SHAP</strong> shows which inputs pushed that prediction up or down.</span>
      <span>This is a statistical signal, not financial advice.</span>
    </>
  ),
  signal: (
    <>
      <span><strong>Confidence</strong> = how sure the model is of its side: <code>max(up probability, 1 − up probability) × 100</code>. 50% is a coin flip.</span>
      <span><strong>Up probability</strong> = the model&apos;s chance that the next trading day closes higher. The bar shows it against the SELL and BUY lines; in between is HOLD.</span>
      <span>The trading bot only acts on BUY/SELL signals with <strong>{BOT_MIN_CONFIDENCE}%+</strong> confidence.</span>
      <span><strong>Automate</strong> opens the Automated page with this asset set up as shown below. Nothing is saved until you press <strong>Save Rules</strong> there.</span>
    </>
  ),
  drivers: (
    <>
      <span><strong>Top drivers (SHAP)</strong>: the inputs that moved this prediction the most.</span>
      <span>Green bars pushed toward &quot;price up&quot;, red bars toward &quot;price down&quot;. The % is each input&apos;s share of the total push.</span>
      <span>Hover a row for details.</span>
    </>
  ),
  price: (
    <>
      <span>Daily closing prices for the last ~30 trading days, the same data the model trains on.</span>
      <span>Hover (or tap, or use the arrow keys) to read the price on each day.</span>
    </>
  ),
  rationale: (
    <>
      <span>The model&apos;s reasoning in words: the price trend, the news sentiment (FinBERT) and the inputs that pushed the forecast most.</span>
    </>
  ),
}

function signalTone(signal) {
  const s = String(signal || '').toUpperCase()
  if (s === 'BUY') return styles.toneBuy
  if (s === 'SELL') return styles.toneSell
  return styles.toneHold
}

function formatMoney(value) {
  const n = Number(value)
  return Number.isFinite(n) ? n.toLocaleString(undefined, usdOptions(n)) : '—'
}

function formatPct(value, digits = 1) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '—'
  return `${n > 0 ? '+' : n < 0 ? '−' : ''}${Math.abs(n).toFixed(digits)}%`
}

function formatDay(iso) {
  const d = new Date(`${iso}T00:00:00`)
  return Number.isNaN(d.getTime()) ? String(iso || '') : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function timeAgo(iso) {
  const t = Date.parse(iso || '')
  if (!Number.isFinite(t)) return null
  const minutes = Math.max(0, Math.round((Date.now() - t) / 60000))
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  return `${Math.round(minutes / 60)} h ago`
}

// Same bands as the backend's news sentiment label.
function sentimentLabel(score) {
  const n = Number(score)
  if (!Number.isFinite(n)) return null
  if (n >= 0.35) return 'Strong bullish'
  if (n >= 0.1) return 'Bullish'
  if (n <= -0.35) return 'Strong bearish'
  if (n <= -0.1) return 'Bearish'
  return 'Neutral'
}

const SPARK_W = 300
const SPARK_H = 80

function Sparkline({ points }) {
  const [active, setActive] = useState(null)
  const data = (points || [])
    .map((p) => ({ date: p.date, close: Number(p.close) }))
    .filter((p) => Number.isFinite(p.close))
  if (data.length < 2) return <p className={styles.muted}>Price history appears after the next AI refresh.</p>

  const closes = data.map((p) => p.close)
  const min = Math.min(...closes)
  const max = Math.max(...closes)
  const span = max - min || 1
  const last = data.length - 1
  const coords = closes.map((v, i) => [(i / last) * SPARK_W, SPARK_H - ((v - min) / span) * (SPARK_H - 8) - 4])
  const line = coords.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const up = closes[last] >= closes[0]

  function pick(event) {
    const rect = event.currentTarget.getBoundingClientRect()
    const ratio = (event.clientX - rect.left) / rect.width
    setActive(Math.min(last, Math.max(0, Math.round(ratio * last))))
  }

  function onKeyDown(event) {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
    event.preventDefault()
    const step = event.key === 'ArrowLeft' ? -1 : 1
    setActive((current) => Math.min(last, Math.max(0, (current ?? (step > 0 ? -1 : last + 1)) + step)))
  }

  const point = active != null ? data[active] : null
  const xPct = active != null ? (active / last) * 100 : 0
  const yPct = active != null ? (coords[active][1] / SPARK_H) * 100 : 0
  const sinceStart = point ? ((point.close - closes[0]) / closes[0]) * 100 : 0
  const tipSide = xPct > 55 ? styles.sparkTipFlip : ''

  return (
    <div className={styles.spark}>
      <div
        className={styles.sparkPlot}
        tabIndex={0}
        role="img"
        aria-label={`Price over ${data.length} days: ${formatMoney(closes[0])} to ${formatMoney(closes[last])}. Use arrow keys to read each day.`}
        onPointerMove={pick}
        onPointerDown={pick}
        onPointerLeave={() => setActive(null)}
        onBlur={() => setActive(null)}
        onKeyDown={onKeyDown}
      >
        <svg viewBox={`0 0 ${SPARK_W} ${SPARK_H}`} preserveAspectRatio="none" className={styles.sparkSvg} aria-hidden="true">
          <path d={`${line} L${SPARK_W},${SPARK_H} L0,${SPARK_H} Z`} className={up ? styles.sparkAreaUp : styles.sparkAreaDown} />
          <path d={line} className={up ? styles.sparkLineUp : styles.sparkLineDown} vectorEffect="non-scaling-stroke" />
        </svg>
        {point ? (
          <>
            <span className={styles.sparkCursor} style={{ left: `${xPct}%` }} />
            <span className={styles.sparkDot} style={{ left: `${xPct}%`, top: `${yPct}%` }} />
            <span className={`${styles.sparkTip} ${tipSide}`} style={{ left: `${xPct}%` }} role="status">
              <span>{formatDay(point.date)}</span>
              <strong>{formatMoney(point.close)}</strong>
              {active > 0 ? <span className={sinceStart >= 0 ? styles.up : styles.down}>{formatPct(sinceStart)} since {formatDay(data[0].date)}</span> : <span>start of window</span>}
            </span>
          </>
        ) : null}
      </div>
      <div className={styles.sparkFoot}>
        <span>{formatDay(data[0].date)} · {formatMoney(closes[0])}</span>
        <span>High {formatMoney(max)} · Low {formatMoney(min)}</span>
        <span>{formatDay(data[last].date)} · {formatMoney(closes[last])}</span>
      </div>
    </div>
  )
}

function Drivers({ drivers, fallback }) {
  const [active, setActive] = useState(null)
  if (fallback) return <p className={styles.muted}>The AI model could not run for this asset, so there are no model drivers.</p>
  if (!drivers?.length) return <p className={styles.muted}>Driver breakdown appears after the next AI refresh.</p>
  const maxAbs = Math.max(...drivers.map((d) => Math.abs(Number(d.impact_pct) || 0)), 1)
  const current = drivers.find((d) => d.feature === active)
  const currentImpact = Number(current?.impact_pct) || 0
  return (
    <div className={styles.driverWrap}>
      <ul className={styles.drivers} onMouseLeave={() => setActive(null)}>
        {drivers.map((d) => {
          const impact = Number(d.impact_pct) || 0
          const width = `${(Math.abs(impact) / maxAbs) * 50}%`
          return (
            <li
              key={d.feature}
              className={`${styles.driverRow} ${active === d.feature ? styles.driverActive : ''}`}
              onMouseEnter={() => setActive(d.feature)}
              onFocus={() => setActive(d.feature)}
              onBlur={() => setActive(null)}
              tabIndex={0}
            >
              <span className={styles.driverLabel}>{d.label}</span>
              <span className={styles.driverTrack} aria-hidden="true">
                <span className={styles.driverMid} />
                <span
                  className={impact >= 0 ? styles.driverBarUp : styles.driverBarDown}
                  style={impact >= 0 ? { left: '50%', width } : { right: '50%', width }}
                />
              </span>
              <span className={`${styles.driverValue} ${impact >= 0 ? styles.up : styles.down}`}>
                {impact >= 0 ? '▲' : '▼'} {Math.abs(impact).toFixed(1)}%
              </span>
            </li>
          )
        })}
      </ul>
      <p className={styles.driverCaption} role="status">
        {current ? (
          <>
            <strong>{current.label}</strong> pushed the forecast toward{' '}
            <strong className={currentImpact >= 0 ? styles.up : styles.down}>price {currentImpact >= 0 ? 'up' : 'down'}</strong>
            {' '}({Math.abs(currentImpact).toFixed(1)}% of the total push).
          </>
        ) : (
          'Hover a row to see what it did.'
        )}
      </p>
    </div>
  )
}

function ProbabilityGauge({ probability, thresholds }) {
  const buy = Number(thresholds?.buy ?? 0.55) * 100
  const sell = Number(thresholds?.sell ?? 0.45) * 100
  const p = Number(probability) * 100
  return (
    <div className={styles.gauge}>
      <div className={styles.gaugeTrack} role="img"
        aria-label={`Up probability ${Number.isFinite(p) ? p.toFixed(0) : 'unknown'}%. SELL at or below ${sell.toFixed(0)}%, BUY at or above ${buy.toFixed(0)}%.`}>
        <span className={styles.gaugeSell} style={{ width: `${sell}%` }} />
        <span className={styles.gaugeBuy} style={{ left: `${buy}%`, width: `${100 - buy}%` }} />
        {Number.isFinite(p) ? <span className={styles.gaugeMarker} style={{ left: `${p}%` }} /> : null}
      </div>
      <div className={styles.gaugeScale}>
        <span>SELL ≤ {sell.toFixed(0)}%</span>
        <span>HOLD</span>
        <span>BUY ≥ {buy.toFixed(0)}%</span>
      </div>
    </div>
  )
}

// One-line strategy summaries for the Automate panel (full rules are in its tooltip).
const STRATEGY_SUMMARY = {
  'Trend Following': 'Rides the trend: buys when the price is rising and the AI says BUY, sells when it is falling and the AI says SELL.',
  'Mean Reversion': 'Bets the price bounces back: buys after it drops 3% or more, sells after it jumps 3% or more, but only when the AI agrees.',
  'News Momentum': 'Follows the headlines: buys on very good news, sells on very bad news, whatever the AI says.',
  'AI Momentum + Sentiment': 'Only trades when the AI and the news point the same way.',
}

function AutomatePlan({ item, suggestion, readiness }) {
  const { strategy, stopLossPct, trailingStopLossPct, takeProfitPct } = suggestion.rule
  const rules = STRATEGY_RULES[strategy]
  const price = Number(item.latest_price)
  const hasPrice = Number.isFinite(price) && price > 0
  const units = unitWord(item.symbol)
  const exits = [
    {
      name: 'Stop-loss',
      pct: stopLossPct,
      note: hasPrice ? `sells at ${formatMoney(price * (1 - stopLossPct / 100))}` : 'below buy price',
    },
    { name: 'Trailing stop', pct: trailingStopLossPct, note: 'drop from peak' },
    {
      name: 'Take-profit',
      pct: takeProfitPct,
      note: hasPrice ? `sells at ${formatMoney(price * (1 + takeProfitPct / 100))}` : 'above buy price',
    },
  ]

  return (
    <div className={styles.automatePanel}>
      <p className={styles.label}>
        What Automate will set up
        <InfoTip label="Automate" align="end">
          <span><strong>Automate</strong> opens the Automated page with {item.symbol} set up like this and its bot switched on. Nothing is saved until you press <strong>Save Rules</strong>; <strong>Undo</strong> reverts it.</span>
          {rules ? <span><strong>{strategy}</strong>: buys when {rules.buy}; sells the bot&apos;s {units} when {rules.sell}.</span> : null}
          <span><strong>Stop-loss</strong>: sells if the price falls {stopLossPct}% below the bot&apos;s buy price. <strong>Trailing stop</strong>: once in profit, sells if the price drops {trailingStopLossPct}% from its highest point. <strong>Take-profit</strong>: sells when the price is {takeProfitPct}% above the buy price.{hasPrice ? ` Dollar levels assume a buy at today's ${formatMoney(price)}.` : ''}</span>
          {suggestion.reasons.map((reason) => <span key={reason}>Why: {reason}</span>)}
        </InfoTip>
      </p>
      <p className={styles.planStrategy}>
        <strong>{strategy}</strong>
        <span>
          {STRATEGY_SUMMARY[strategy] || suggestion.reasons[0]}{' '}
          <span className={readiness.ready ? styles.readyOn : styles.readyOff}>{readiness.text}</span>
        </span>
      </p>
      <div className={styles.exitTiles}>
        {exits.map((exit) => (
          <div key={exit.name} className={styles.exitTile}>
            <span>{exit.name}</span>
            <strong>{exit.pct}%</strong>
            <small>{exit.note}</small>
          </div>
        ))}
      </div>
    </div>
  )
}

function InsightCard({ item, onAutomate }) {
  const signal = String(item.signal || 'HOLD').toUpperCase()
  const unavailable = Boolean(item.unavailable)
  const fallback = Boolean(item.model_fallback)
  const confidence = item.confidence
  const probUp = item.probability_up != null ? Number(item.probability_up) * 100 : null
  const botReady = !fallback && (signal === 'BUY' || signal === 'SELL') && Number(confidence) >= BOT_MIN_CONFIDENCE
  const suggestion = unavailable ? null : suggestBotConfig(item)
  const readiness = {
    ready: botReady,
    text: botReady
      ? 'Strong enough for the bot to act.'
      : fallback
        ? 'The bot skips rule-based estimates.'
        : `The bot waits: needs a BUY/SELL at ${BOT_MIN_CONFIDENCE}%+ confidence.`,
  }
  const updated = timeAgo(item.generated_at)
  const newsLabel = unavailable ? null : sentimentLabel(item.latest_sentiment_score)
  const verdictTone = signal === 'BUY' ? styles.verdictBuy : signal === 'SELL' ? styles.verdictSell : ''
  // The trend and news lines are already shown as chips above the list.
  const rationale = (item.rationale || []).filter((r) => !(newsLabel && /^(price trend|news sentiment):/i.test(r)))

  return (
    <section className={styles.card} aria-label={`${item.symbol} insight`}>
      <div className={styles.cardHead}>
        <div className={styles.symbolRow}>
          <h2 className={styles.asset}>{item.symbol}</h2>
          {item.latest_price != null ? <span className={styles.price}>{formatMoney(item.latest_price)}</span> : null}
          {item.price_change_pct != null ? (
            <span className={`${styles.changeChip} ${Number(item.price_change_pct) >= 0 ? styles.chipUp : styles.chipDown}`}>
              {formatPct(item.price_change_pct)} 1M
            </span>
          ) : null}
        </div>
        {updated ? <span className={styles.muted}>Updated {updated}</span> : null}
      </div>

      <div className={styles.cardBody}>
        <div className={styles.panel}>
          <p className={styles.label}>Price · last 30 days<InfoTip label="Price chart">{TIPS.price}</InfoTip></p>
          <Sparkline points={item.price_history} />
        </div>

        <div className={styles.panel}>
          <p className={styles.label}>
            {unavailable ? 'Model drivers' : `Why the model says ${signal}`}
            <InfoTip label="Top drivers">{TIPS.drivers}</InfoTip>
          </p>
          <Drivers drivers={item.drivers} fallback={fallback || unavailable} />
        </div>

        <div className={`${styles.verdict} ${verdictTone}`}>
          <p className={styles.label}>
            {fallback ? 'Rule-based estimate' : 'AI signal'}
            <InfoTip label="AI signal" align="end">{TIPS.signal}</InfoTip>
          </p>
          <p className={styles.scoreLine}>
            {confidence != null ? <span className={styles.scoreValue}>{confidence}%</span> : null}
            <span className={`${styles.scoreSignal} ${signalTone(signal)}`}>{unavailable ? 'N/A' : signal}</span>
          </p>
          {probUp != null ? (
            <>
              <p className={styles.verdictText}>
                <strong>{probUp.toFixed(0)}%</strong> chance it closes higher next trading day
              </p>
              <ProbabilityGauge probability={item.probability_up} thresholds={item.thresholds} />
            </>
          ) : (
            <p className={styles.verdictText}>
              {fallback ? 'The AI model could not run, so this is estimated from the price trend and news only.' : 'No forecast available right now.'}
            </p>
          )}
          {suggestion ? (
            <div className={styles.verdictAction}>
              <button type="button" className={styles.primary} onClick={() => onAutomate(suggestion)}>
                Automate {item.symbol} →
              </button>
            </div>
          ) : null}
        </div>
      </div>

      <div className={styles.cardDetail}>
        <div className={styles.rationalePanel}>
          <p className={styles.label}>Rationale<InfoTip label="Rationale">{TIPS.rationale}</InfoTip></p>
          {item.trend_summary ? <p className={styles.trendSummary}>{item.trend_summary}</p> : null}
          {newsLabel ? (
            <p className={styles.context}>
              <span className={styles.contextChip}>
                News sentiment <strong>{newsLabel}</strong> {Number(item.latest_sentiment_score).toFixed(2)}
              </span>
              {item.price_change_pct != null ? (
                <span className={styles.contextChip}>
                  1-month trend <strong className={Number(item.price_change_pct) >= 0 ? styles.up : styles.down}>{formatPct(item.price_change_pct)}</strong>
                </span>
              ) : null}
            </p>
          ) : null}
          {rationale.length ? (
            <ul className={styles.rationale}>
              {rationale.map((r) => <li key={r}>{r}</li>)}
            </ul>
          ) : (
            <p className={styles.muted}>No written rationale for this asset yet.</p>
          )}
        </div>

        {suggestion ? <AutomatePlan item={item} suggestion={suggestion} readiness={readiness} /> : null}
      </div>
    </section>
  )
}

export default function Insights() {
  const router = useRouter()
  const { data, error, isLoading } = useSWR('/api/ml/v2/watchlist/insights')
  const items = Array.isArray(data) ? data : []
  const loading = isLoading && !items.length
  const errorMessage = error?.message || ''
  const counts = items.reduce((acc, it) => {
    const s = String(it.signal || '').toUpperCase()
    acc[s] = (acc[s] || 0) + 1
    return acc
  }, {})

  function automate(suggestion) {
    storeAutoConfig(suggestion)
    router.push('/automated')
  }

  return (
    <AppShell title="AI Insights" subtitle="Sentiment & recommendations">
      <div className={styles.list}>
        {items.length ? (
          <div className={styles.summaryBar}>
            <span className={styles.summaryTitle}>
              {items.length} asset{items.length === 1 ? '' : 's'} analysed
              <InfoTip label="How AI insights work">{TIPS.page}</InfoTip>
            </span>
            <span className={`${styles.countChip} ${styles.chipUp}`}>{counts.BUY || 0} BUY</span>
            <span className={`${styles.countChip} ${styles.chipDown}`}>{counts.SELL || 0} SELL</span>
            <span className={styles.countChip}>{counts.HOLD || 0} HOLD</span>
          </div>
        ) : null}
        {loading ? <section className={styles.emptyState}>Loading live watchlist insights...</section> : null}
        {!loading && errorMessage ? <section className={styles.emptyState}>Error: {errorMessage}</section> : null}
        {!loading && !errorMessage && !items.length ? (
          <section className={styles.emptyState}>
            No watchlist insights available yet. Add assets on <Link href="/watchlist">Watchlist</Link> and optionally save a NewsAPI key on <Link href="/api-keys">API Management</Link>.
          </section>
        ) : null}
        {items.map((item) => <InsightCard key={item.symbol} item={item} onAutomate={automate} />)}
      </div>
    </AppShell>
  )
}
