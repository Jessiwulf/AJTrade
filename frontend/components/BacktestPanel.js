import { useMemo, useState } from 'react'
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import InfoTip from './InfoTip'
import { apiFetch } from '../lib/api'
import { tunedBotConfig } from '../lib/autoConfig'
import { formatQuantity, usdOptions } from '../lib/format'
import styles from '../styles/Backtest.module.css'

const PERIODS = [
  { key: '3m', label: '3M' },
  { key: '6m', label: '6M' },
  { key: '1y', label: '1Y' },
  { key: '5y', label: '5Y' },
  { key: '10y', label: '10Y' },
  { key: '20y', label: '20Y' },
]
// Rough first-run time (one CPU core), shown while it runs.
const PERIOD_TIME = { '3m': 'a few seconds', '6m': 'a few seconds', '1y': 'about 5 seconds', '5y': 'about 10 seconds', '10y': 'about 20 seconds', '20y': 'up to a minute' }
const MAX_CHART_POINTS = 600
const MAX_TRADE_ROWS = 300

// Every Nth day for long periods (always keeping the last), so a 20-year chart stays light.
function thin(points) {
  if (!Array.isArray(points) || points.length <= MAX_CHART_POINTS) return points || []
  const step = Math.ceil(points.length / MAX_CHART_POINTS)
  const out = points.filter((_, i) => i % step === 0)
  if (out[out.length - 1] !== points[points.length - 1]) out.push(points[points.length - 1])
  return out
}

const RULE_KEYS = ['strategy', 'stopLossPct', 'trailingStopLossPct', 'takeProfitPct', 'maxCapitalPerTrade', 'dcaIntervalHours']

function money(value) {
  const n = Number(value)
  return Number.isFinite(n) ? n.toLocaleString(undefined, usdOptions(n)) : '--'
}

function pct(value, { signed = true } = {}) {
  const n = Number(value)
  if (value == null || !Number.isFinite(n)) return '--'
  return `${signed && n > 0 ? '+' : ''}${n.toFixed(1)}%`
}

function tone(value) {
  const n = Number(value)
  if (!Number.isFinite(n) || n === 0) return ''
  return n > 0 ? styles.pos : styles.neg
}

function ruleFor(rule) {
  return Object.fromEntries(RULE_KEYS.map((key) => [key, rule?.[key]]))
}

const TIP = (
  <>
    <span><strong>A backtest replays these exact settings over past prices</strong> to show what the bot would have done: every buy, sell and exit, and where the money would have ended up.</span>
    <span><strong>No peeking at the future</strong>: on each day the AI is trained only on the 180 days before it (retrained weekly), and a decision made at a day&apos;s close is filled at the next day&apos;s open.</span>
    <span><strong>Exits</strong> use each day&apos;s high and low. If a stop and the take-profit could both have hit on the same day, the stop is assumed to come first.</span>
    <span><strong>Buy &amp; hold</strong> puts the whole starting cash into the asset on day one: the simplest strategy to beat.</span>
    <span><strong>Max drawdown</strong> is the biggest fall from a high point, i.e. the worst stretch you would have sat through.</span>
    <span>Past results do not guarantee future results, and this is not investment advice.</span>
  </>
)

function GateLine({ gate, unsaved, rule, checkOn }) {
  if (!checkOn) {
    return (
      <p className={`${styles.gate} ${styles.gateWait}`}>
        Backtest check is off for this asset: the bot buys on the AI outlook (70%+ confidence) without checking these settings against the past year.
        {unsaved ? ' Press Save Rules to apply.' : ''}
      </p>
    )
  }
  if (!gate) return null
  const res = gate.rule_result
  const of = res ? `${Number(res.win_rate || 0).toFixed(0)}% of ${res.trades} simulated trades closed in profit` : ''
  const needs = `${rule?.min_win_rate ?? 60}% of ${rule?.min_trades ?? 3}+`
  const lines = {
    pass: { cls: styles.gateOk, text: `✓ Backtest check passed: over the past year ${of} (needs ${needs}), so the bot may buy on AI outlooks.` },
    fail: { cls: styles.gateBad, text: `✗ Backtest check not passed: over the past year ${of} (needs ${needs}). The bot will not buy on AI outlooks until it passes; selling and exits still work. Try Auto-tune below.` },
    pending: { cls: styles.gateWait, text: '… Your saved settings are being backtested in the background; the bot waits to buy until the result is in.' },
    off: { cls: styles.gateWait, text: 'Dollar-cost averaging ignores the AI outlook, so the backtest check does not apply.' },
    no_rule: { cls: styles.gateWait, text: 'Save a bot for this asset to get its backtest check.' },
    disabled: { cls: styles.gateWait, text: 'Backtest check is on again. Press Save Rules and the saved settings are re-tested within a minute or two.' },
  }
  const line = lines[gate.gate]
  if (!line) return null
  return (
    <p className={`${styles.gate} ${line.cls}`}>
      {line.text}
      {unsaved && gate.gate !== 'no_rule' ? ' (Unsaved changes are tested after you press Save Rules.)' : ''}
    </p>
  )
}

export default function BacktestPanel({ asset, rule, disabled, onApplyTuned, gate, unsaved, gateRule, checkOn = true, onToggleCheck }) {
  const [period, setPeriod] = useState('1y')
  const [startCash, setStartCash] = useState(10000)
  const [state, setState] = useState({ status: 'idle', result: null, error: '', ranWith: null })
  const [tune, setTune] = useState({ status: 'idle', message: '' })

  // Finds strategy + exits that pass the backtest check (best simulated return) and puts them in the form as
  // unsaved changes, so the user can backtest, save or undo them.
  async function autoTune() {
    setTune({ status: 'running', message: '' })
    try {
      const tuned = await apiFetch(`/api/bot/autoconfig?symbol=${encodeURIComponent(asset)}`)
      const config = tunedBotConfig({ symbol: asset }, tuned)
      onApplyTuned?.(config)
      setTune({
        status: 'done',
        message: tuned.reached
          ? `Applied: ${config.rule.strategy} with exits that passed the backtest check. Run the backtest to review them, then Save Rules. Past results do not guarantee future results.`
          : `No settings passed the backtest check on ${asset}; applied the closest. Run the backtest to review them before saving.`,
      })
    } catch (err) {
      setTune({ status: 'error', message: err?.message || 'Auto-tune failed.' })
    }
  }
  const current = useMemo(() => JSON.stringify({ asset, period, startCash, rule: ruleFor(rule) }), [asset, period, startCash, rule])
  const stale = state.result && state.ranWith !== current
  const result = state.result && state.result.symbol === asset ? state.result : null

  async function run() {
    setState((prev) => ({ ...prev, status: 'running', error: '' }))
    try {
      const data = await apiFetch('/api/bot/backtest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: asset, period, startCash: Number(startCash) || 10000, rule: ruleFor(rule) }),
      })
      setState({ status: 'done', result: data, error: '', ranWith: current })
    } catch (err) {
      setState((prev) => ({ ...prev, status: 'error', error: err?.message || 'Backtest failed.' }))
    }
  }

  const m = result?.metrics
  const chartData = useMemo(() => thin(result?.equity), [result])
  const multiYear = (result?.test_days || 0) > 400
  const beat = m ? Number(m.return_pct) - Number(m.hold_return_pct) : null

  return (
    <div className={styles.panel}>
      <div className={styles.head}>
        <p className={styles.title}>
          Backtest these settings
          <InfoTip label="Backtest" wide>{TIP}</InfoTip>
        </p>
        <span className={styles.sub}>See how the bot would have traded {disabled ? 'an asset' : asset} in the past, before it trades for real.</span>
      </div>
      {disabled ? null : (
        <div className={styles.gateRow}>
          <GateLine gate={gate} unsaved={unsaved} rule={gateRule} checkOn={checkOn} />
          {onToggleCheck && rule?.strategy !== 'Dollar-Cost Averaging' ? (
            <label className={styles.switch}>
              <input type="checkbox" checked={checkOn} onChange={(e) => onToggleCheck(e.target.checked)} />
              <span aria-hidden="true" className={styles.switchTrack}><span className={styles.switchThumb} /></span>
              <span>Backtest check {checkOn ? 'on' : 'off'}</span>
            </label>
          ) : null}
        </div>
      )}

      <div className={styles.controls}>
        <div className={styles.segment} role="group" aria-label="Backtest period">
          {PERIODS.map((p) => (
            <button key={p.key} type="button" className={period === p.key ? styles.segmentOn : ''} onClick={() => setPeriod(p.key)} aria-pressed={period === p.key}>
              {p.label}
            </button>
          ))}
        </div>
        <label className={styles.cash}>
          <span>Starting cash ($)</span>
          <input type="number" min={100} step={100} value={startCash} onChange={(e) => setStartCash(e.target.value)} />
        </label>
        <button type="button" className={styles.run} onClick={run} disabled={disabled || state.status === 'running'}>
          {state.status === 'running' ? 'Running...' : result ? 'Run again' : 'Run backtest'}
        </button>
        {onApplyTuned ? (
          <button type="button" className={styles.tune} onClick={autoTune} disabled={disabled || tune.status === 'running'}>
            {tune.status === 'running' ? 'Tuning...' : 'Auto-tune settings'}
          </button>
        ) : null}
      </div>
      {tune.status === 'running' ? <p className={styles.note}>Backtesting about 400 combinations of strategy and exits on the past year. This takes around 10 seconds.</p> : null}
      {tune.message ? <p className={tune.status === 'error' ? styles.error : styles.note}>{tune.message}</p> : null}
      {state.status === 'running' ? (
        <p className={styles.note}>
          Replaying day by day and re-training the AI as it goes. The first {PERIODS.find((p) => p.key === period)?.label} run of an asset takes {PERIOD_TIME[period]}; changing only the settings afterwards takes a second or two.
          {['5y', '10y', '20y'].includes(period) ? ' Long periods test the AI on prices only: AJTrade has news for the last 30 days.' : ''}
        </p>
      ) : null}
      {state.error ? <p className={styles.error}>{state.error}</p> : null}

      {result && m ? (
        <div className={styles.results} aria-live="polite">
          {stale ? <p className={styles.stale}>Settings changed since this run. Press Run again to update it.</p> : null}
          <p className={styles.headline}>
            From {result.start} to {result.end}, the bot would have turned <strong>{money(m.start_cash)}</strong> into{' '}
            <strong className={tone(m.return_pct)}>{money(m.final_equity)} ({pct(m.return_pct)})</strong>.{' '}
            Buying and holding {result.symbol}: <strong className={tone(m.hold_return_pct)}>{pct(m.hold_return_pct)}</strong>
            {beat != null ? <>, so the bot did<strong className={tone(beat)}>{beat >= 0 ? 'better' : 'worse'} by {Math.abs(beat).toFixed(1)} points</strong>.</> : '.'}
          </p>

          <div className={styles.tiles}>
            <div><span>Bot return</span><strong className={tone(m.return_pct)}>{pct(m.return_pct)}</strong><small>Realized {money(m.realized_pl)}</small></div>
            <div><span>Buy &amp; hold</span><strong className={tone(m.hold_return_pct)}>{pct(m.hold_return_pct)}</strong><small>Same cash, bought day one</small></div>
            <div><span>Max drawdown</span><strong className={m.max_drawdown_pct < 0 ? styles.neg : ''}>{pct(m.max_drawdown_pct)}</strong><small>Buy &amp; hold {pct(m.hold_max_drawdown_pct)}</small></div>
            <div><span>Closed in profit</span><strong>{pct(m.win_rate, { signed: false })}</strong><small>{m.winning_trades} of {m.closed_trades} simulated trades</small></div>
            <div><span>Trades</span><strong>{m.buys + m.sells}</strong><small>{m.buys} buys · {m.sells} sells · in market {m.exposure_pct}%</small></div>
            <div><span>AI direction right</span><strong>{pct(result.model?.direction_accuracy_pct, { signed: false })}</strong><small>of {result.model?.days} days (50% = coin flip)</small></div>
          </div>

          <div className={styles.chart}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 4 }}>
                <CartesianGrid vertical={false} stroke="var(--aj-border)" strokeDasharray="3 3" />
                <XAxis dataKey="date" tick={{ fill: 'var(--aj-text-muted)', fontSize: 11 }} tickLine={false} axisLine={false} minTickGap={36}
                  tickFormatter={(d) => new Date(`${d}T00:00:00Z`).toLocaleDateString(undefined, multiYear ? { month: 'short', year: '2-digit', timeZone: 'UTC' } : { month: 'short', day: 'numeric', timeZone: 'UTC' })} />
                <YAxis tick={{ fill: 'var(--aj-text-muted)', fontSize: 11 }} tickLine={false} axisLine={false} width={64} domain={['auto', 'auto']}
                  tickFormatter={(v) => `$${(Number(v) / 1000).toFixed(1)}k`} />
                <Tooltip formatter={(v, name) => [money(v), name]} labelFormatter={(d) => new Date(`${d}T00:00:00Z`).toLocaleDateString(undefined, { timeZone: 'UTC' })} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Line type="monotone" dataKey="bot" name="Bot" stroke="var(--aj-cta-to)" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="hold" name="Buy & hold" stroke="var(--aj-text-muted)" strokeWidth={1.5} strokeDasharray="5 4" dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>

          {result.open_position ? (
            <p className={styles.note}>
              Still holding at the end: {formatQuantity(result.open_position.qty)} worth {money(result.open_position.value)} (unrealized {money(result.open_position.unrealized_pl)}), counted in the bot&apos;s final value.
            </p>
          ) : null}
          {m.skipped_no_cash ? <p className={styles.note}>{m.skipped_no_cash} buys were skipped because the starting cash had run out.</p> : null}
          {(result.warnings || []).map((w) => <p key={w} className={styles.warn}>{w}</p>)}

          <details className={styles.trades}>
            <summary>
              {result.trades.length > MAX_TRADE_ROWS ? `Latest ${MAX_TRADE_ROWS} of ${result.trades.length}` : `All ${result.trades.length}`} simulated trades
            </summary>
            {result.trades.length ? (
              <div className={styles.tableWrap}>
                <table>
                  <thead>
                    <tr><th>Date</th><th>Side</th><th>Price</th><th>Qty</th><th>Amount</th><th>P&amp;L</th><th>Why</th></tr>
                  </thead>
                  <tbody>
                    {[...result.trades].reverse().slice(0, MAX_TRADE_ROWS).map((t, index) => (
                      <tr key={`${t.date}-${t.side}-${index}`}>
                        <td>{t.date}</td>
                        <td className={t.side === 'BUY' ? styles.pos : styles.neg}>{t.side}</td>
                        <td>{money(t.price)}</td>
                        <td>{formatQuantity(t.qty)}</td>
                        <td>{money(t.amount)}</td>
                        <td className={t.pl == null ? '' : tone(t.pl)}>{t.pl == null ? '--' : money(t.pl)}</td>
                        <td className={styles.why}>{t.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <p className={styles.note}>The bot would not have traded: its conditions were never met in this period.</p>}
          </details>

          <p className={styles.fine}>
            Assumes {result.assumptions.fee_pct}% fee and {result.assumptions.slippage_pct}% slippage per trade, {money(result.assumptions.trade_amount)} per trade,
            one decision a day, and the AI outlook needing {result.assumptions.min_confidence}%+ confidence. News sentiment was known for {result.assumptions.news_days} of the {result.test_days} days.
            Past results do not guarantee future results. Not investment advice.
          </p>
        </div>
      ) : null}
    </div>
  )
}
