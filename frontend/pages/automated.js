import { useEffect, useMemo, useRef, useState } from 'react'
import useSWR from 'swr'
import AppShell from '../components/AppShell'
import BotBehaviorPreview, { DCA_INTERVALS, DCA_STRATEGY, STRATEGY_RULES } from '../components/BotBehaviorPreview'
import { apiFetch } from '../lib/api'
import { takeAutoConfig } from '../lib/autoConfig'
import { usdOptions } from '../lib/format'
import styles from '../styles/Automated.module.css'

const DEFAULT_ASSET = 'NO_ASSET'

const DEFAULT_RULESET = {
  botActive: false,
  executionMode: 'paper',
  selectedAsset: DEFAULT_ASSET,
  updatedAt: null,
  assetRules: {
    [DEFAULT_ASSET]: {
      isActive: false,
      strategy: 'Trend Following',
      stopLossPct: 2,
      trailingStopLossPct: 1.2,
      takeProfitPct: 5,
      maxCapitalPerTrade: 1000,
      maxDailyLossLimit: 500,
      dcaIntervalHours: 24,
    },
  },
  activePositions: [],
  executionLogs: [],
}

const STRATEGIES = ['Trend Following', 'Mean Reversion', 'News Momentum', 'AI Momentum + Sentiment', DCA_STRATEGY]

const STRATEGY_INFO = {
  'Trend Following': 'Follows the AI signal only when it agrees with the 1-month price trend.',
  'Mean Reversion': 'Buys dips and sells spikes: follows the AI signal only after a sharp move the other way.',
  'News Momentum': 'Trades on the news alone, using FinBERT sentiment instead of the AI price forecast.',
  'AI Momentum + Sentiment': 'Follows the AI signal only when news sentiment points the same way.',
  [DCA_STRATEGY]: 'For long-term saving: buys a fixed amount on a schedule and ignores AI signals. Pair it with exits turned off.',
}

// The slider covers the everyday range precisely; typing allows up to `max` for long-term holders.
// Switching an exit off saves 0 (the backend treats 0 as "no exit").
const PCT_LIMITS = {
  stopLossPct: { min: 0.2, sliderMax: 20, max: 90, fallback: 2 },
  trailingStopLossPct: { min: 0.1, sliderMax: 30, max: 90, fallback: 1.2 },
  takeProfitPct: { min: 0.5, sliderMax: 50, max: 1000, fallback: 5 },
}

const RISK_INFO = {
  stopLoss: 'Closes the position if price falls this far below your entry price. It is a fixed floor that caps the loss on any single trade.',
  trailingStop: 'A stop that follows price upward. It sits this % below the highest price reached and never moves down, locking in gains as the trade moves in your favour.',
  takeProfit: 'Closes the position automatically once it gains this % from entry, so profits are banked instead of given back.',
  maxCapital: 'The most money the bot may commit to a single order. Orders requesting more than this are rejected.',
  maxDailyLoss: 'Once today’s realised losses reach this amount, the bot stops opening new trades until the next day.',
}

function InfoTip({ id, label, children, wide = false }) {
  return (
    <span className={styles.infoTip}>
      <button type="button" className={styles.infoBtn} aria-label={`About ${label}`} aria-describedby={id}>
        i
      </button>
      <span role="tooltip" id={id} className={`${styles.tooltip} ${wide ? styles.tooltipWide : ''}`}>
        {children}
      </span>
    </span>
  )
}

function FieldLabel({ tipId, children, tip, wideTip = false }) {
  return (
    <div className={styles.labelRow}>
      <div className={styles.label}>{children}</div>
      <InfoTip id={tipId} label={children} wide={wideTip}>
        {tip}
      </InfoTip>
    </div>
  )
}
function roundTenth(value) {
  return Math.round(value * 10) / 10
}

function PercentField({ value, min, sliderMax, max, fallback, onChange, label }) {
  const enabled = Number(value) > 0
  const [text, setText] = useState(() => Number(value).toFixed(1))
  const [focused, setFocused] = useState(false)
  // Value to restore when the exit is switched back on.
  const [lastOn, setLastOn] = useState(() => (Number(value) > 0 ? Number(value) : fallback))

  useEffect(() => {
    if (!focused) setText(Number(value).toFixed(1))
    if (Number(value) > 0) setLastOn(Number(value))
  }, [value, focused])

  function handleType(raw) {
    setText(raw)
    const next = Number(raw)
    // Move the slider live while the typed value is valid; out-of-range input is clamped on blur.
    if (raw !== '' && Number.isFinite(next) && next >= min && next <= max) onChange(roundTenth(next))
  }

  function commit(raw) {
    setFocused(false)
    const next = Number(raw)
    const clamped = raw === '' || !Number.isFinite(next)
      ? Number(value)
      : Math.min(Math.max(roundTenth(next), min), max)
    onChange(clamped)
    setText(clamped.toFixed(1))
  }

  return (
    <div className={`${styles.sliderRow} ${styles.sliderRowToggle} ${enabled ? '' : styles.sliderRowOff}`}>
      <input
        className={styles.range}
        type="range"
        min={min}
        max={sliderMax}
        step={0.1}
        value={enabled ? Math.min(Number(value), sliderMax) : min}
        onChange={(event) => onChange(Number(event.target.value))}
        disabled={!enabled}
        aria-label={`${label} slider`}
      />
      {enabled ? (
        <label className={styles.pctBox}>
          <input
            className={styles.pctInput}
            type="number"
            inputMode="decimal"
            min={min}
            max={max}
            step={0.1}
            value={text}
            // Size the input to its digits so "5.0 %" sits centered in the pill.
            style={{ width: `${Math.max(String(text).length, 2)}ch` }}
            onFocus={() => setFocused(true)}
            onChange={(event) => handleType(event.target.value)}
            onBlur={(event) => commit(event.target.value)}
            onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur() }}
            aria-label={`${label} (${min} to ${max})`}
          />
          <span aria-hidden="true">%</span>
        </label>
      ) : (
        <div className={`${styles.pctBox} ${styles.pctBoxOff}`}>Off</div>
      )}
      <button
        type="button"
        className={`${styles.switch} ${enabled ? styles.switchOn : ''}`}
        onClick={() => onChange(enabled ? 0 : lastOn)}
        aria-label={`${enabled ? 'Turn off' : 'Turn on'} ${label}`}
        aria-pressed={enabled}
      >
        <span className={styles.knob} />
      </button>
    </div>
  )
}

function RangeTip({ text, limits, offText }) {
  return (
    <>
      {text}
      <span className={styles.tooltipRange}>
        Slider {limits.min}% to {limits.sliderMax}%; type up to {limits.max}%. Switch it off for {offText}.
      </span>
    </>
  )
}

function formatExitPct(value) {
  return Number(value) > 0 ? `${value}%` : 'Off'
}

const TABS = {
  POSITIONS: 'positions',
  LOGS: 'logs',
}

function formatCurrency(value) {
  const amount = Number(value)
  if (!Number.isFinite(amount)) return '$0.00'
  return amount.toLocaleString(undefined, usdOptions(amount))
}

function formatSignedCurrency(value) {
  const amount = Number(value)
  if (!Number.isFinite(amount)) return '$0.00'
  return `${amount >= 0 ? '+' : '-'}${formatCurrency(Math.abs(amount))}`
}

function normalizeRules(raw) {
  if (!raw || typeof raw !== 'object') return DEFAULT_RULESET
  return {
    ...DEFAULT_RULESET,
    ...raw,
    assetRules: {
      ...DEFAULT_RULESET.assetRules,
      ...(raw.assetRules && typeof raw.assetRules === 'object' ? raw.assetRules : {}),
    },
    activePositions: Array.isArray(raw.activePositions) ? raw.activePositions : [],
    executionLogs: Array.isArray(raw.executionLogs) ? raw.executionLogs : [],
  }
}

function ensureAssetRule(rules, asset) {
  const existing = rules.assetRules?.[asset]
  if (existing) return existing
  return {
    isActive: false,
    strategy: 'Trend Following',
    stopLossPct: 2,
    trailingStopLossPct: 1.2,
    takeProfitPct: 5,
    maxCapitalPerTrade: 1000,
    maxDailyLossLimit: 500,
    dcaIntervalHours: 24,
  }
}

// Let load failures surface as SWR errors: silently returning defaults would show "bot off, 2/1.2/5%"
// as if those were the saved rules, and saving that draft would overwrite the real ones.
async function fetchBotRules() {
  const data = await apiFetch('/api/bot/rules')
  return normalizeRules(data)
}

const RULE_FIELDS = [
  'isActive',
  'strategy',
  'stopLossPct',
  'trailingStopLossPct',
  'takeProfitPct',
  'maxCapitalPerTrade',
  'maxDailyLossLimit',
  'dcaIntervalHours',
]

function sameField(a, b) {
  if (typeof a === 'number' || typeof b === 'number') return Number(a) === Number(b)
  if (typeof a === 'boolean' || typeof b === 'boolean') return Boolean(a) === Boolean(b)
  return (a ?? '') === (b ?? '')
}

// True when the draft differs from the saved rules. An asset the user never edited compares as the
// default rule on both sides, so merely viewing an asset is not a change.
function rulesDiffer(draft, saved) {
  if ((draft.executionMode || 'paper') !== (saved.executionMode || 'paper')) return true
  const assets = new Set([...Object.keys(draft.assetRules || {}), ...Object.keys(saved.assetRules || {})])
  assets.delete(DEFAULT_ASSET)
  for (const asset of assets) {
    const a = ensureAssetRule(draft, asset)
    const b = ensureAssetRule(saved, asset)
    if (RULE_FIELDS.some((field) => !sameField(a[field], b[field]))) return true
  }
  return false
}

const SELECTED_ASSET_KEY = 'ajtrade.automated.selectedAsset'

export default function Automated() {
  const [tab, setTab] = useState(TABS.POSITIONS)
  const [saveState, setSaveState] = useState('idle')
  // Which asset is being viewed. Kept apart from the rules draft so switching assets is not an edit,
  // and remembered across refreshes.
  const [pickedAsset, setPickedAsset] = useState(null)
  // Saved rule sets from before each save on this visit, for "Undo last save".
  const [saveHistory, setSaveHistory] = useState([])
  // A setup handed over by the AI Insights "Automate" button, applied once the saved rules have loaded.
  const pendingAutoConfig = useRef(null)
  const [autoConfigNotice, setAutoConfigNotice] = useState(null)
  const [runState, setRunState] = useState('idle')
  const [runMessage, setRunMessage] = useState('')

  const { data: watchlistData } = useSWR('/api/watchlist', (url) => apiFetch(url), {
    dedupingInterval: 5 * 60 * 1000,
    revalidateOnFocus: false,
    revalidateOnReconnect: false,
    revalidateIfStale: false,
    shouldRetryOnError: false,
    keepPreviousData: true,
  })

  const watchlistSymbols = useMemo(
    () => (Array.isArray(watchlistData) ? watchlistData : []).map((item) => String(item.symbol || '').toUpperCase()).filter(Boolean),
    [watchlistData],
  )

  const { data, error: rulesError, mutate, isLoading } = useSWR('bot-rules', fetchBotRules, {
    dedupingInterval: 10 * 1000,
    revalidateOnFocus: true,
    revalidateOnReconnect: false,
    revalidateIfStale: true,
    shouldRetryOnError: false,
    keepPreviousData: true,
    refreshInterval: 15 * 1000,
  })

  const rules = useMemo(() => normalizeRules(data), [data])
  const [draftRules, setDraftRules] = useState(DEFAULT_RULESET)

  // The saved rules the draft was last loaded from (null until the first successful load). The draft
  // is compared against this, not against the placeholder defaults it starts with.
  const [draftBase, setDraftBase] = useState(null)
  const draftRef = useRef(draftRules)
  draftRef.current = draftRules
  const draftBaseRef = useRef(draftBase)
  draftBaseRef.current = draftBase

  const hasChanges = useMemo(
    () => Boolean(draftBase) && rulesDiffer(draftRules, rules),
    [draftBase, draftRules, rules],
  )

  // Load saved rules into the draft: always on the first load, and on later refetches (every 15s)
  // only while the user has no unsaved edits relative to what was last loaded.
  useEffect(() => {
    if (!data) return
    const base = draftBaseRef.current
    if (base && rulesDiffer(draftRef.current, base)) return
    const next = normalizeRules(data)
    setDraftRules(next)
    setDraftBase(next)
  }, [data])

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(SELECTED_ASSET_KEY)
      if (saved) setPickedAsset(saved)
    } catch {
      // Storage unavailable (private mode etc.): fall back to the default asset.
    }
    pendingAutoConfig.current = takeAutoConfig()
  }, [])

  // Apply the AI Insights setup as unsaved changes on top of the saved rules (so Save Rules / Undo apply).
  useEffect(() => {
    const config = pendingAutoConfig.current
    if (!draftBase || !config) return
    pendingAutoConfig.current = null
    selectAsset(config.asset)
    setSaveState('idle')
    setDraftRules((prev) => ({
      ...prev,
      assetRules: {
        ...prev.assetRules,
        [config.asset]: { ...ensureAssetRule(prev, config.asset), ...config.rule },
      },
    }))
    setAutoConfigNotice(config)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftBase])

  function selectAsset(asset) {
    setPickedAsset(asset)
    try {
      window.localStorage.setItem(SELECTED_ASSET_KEY, asset)
    } catch {
      // Not persisted; selection still works for this visit.
    }
  }

  const pickedIsValid = pickedAsset
    && pickedAsset !== DEFAULT_ASSET
    && (!watchlistSymbols.length || watchlistSymbols.includes(pickedAsset) || Boolean(rules.assetRules?.[pickedAsset]))
  const selectedAsset = pickedIsValid
    ? pickedAsset
    : (rules.selectedAsset && rules.selectedAsset !== DEFAULT_ASSET
      ? rules.selectedAsset
      : (watchlistSymbols[0] || DEFAULT_ASSET))

  const assetRule = useMemo(() => ensureAssetRule(draftRules, selectedAsset), [draftRules, selectedAsset])

  const assetOptions = useMemo(() => {
    const base = watchlistSymbols.length ? [...watchlistSymbols] : [DEFAULT_ASSET]
    if (selectedAsset !== DEFAULT_ASSET && !base.includes(selectedAsset)) {
      return [selectedAsset, ...base]
    }
    return base
  }, [watchlistSymbols, selectedAsset])

  const activePositions = useMemo(
    () => (rules.activePositions || []).filter((position) => selectedAsset === DEFAULT_ASSET || position.asset === selectedAsset),
    [rules, selectedAsset],
  )

  const executionLogs = useMemo(
    () => (rules.executionLogs || []).filter((log) => selectedAsset === DEFAULT_ASSET || log.asset === selectedAsset),
    [rules, selectedAsset],
  )

  async function saveRules(nextRules, { recordHistory = true } = {}) {
    const payload = normalizeRules(nextRules)
    const previous = draftBaseRef.current
    setSaveState('saving')
    try {
      const botActive = Object.values(payload.assetRules).some((rule) => rule?.isActive)
      const server = await apiFetch('/api/bot/rules', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...payload, selectedAsset, botActive, updatedAt: new Date().toISOString() }),
      })
      const normalized = normalizeRules(server || payload)
      setDraftRules(normalized)
      setDraftBase(normalized)
      if (recordHistory && previous) setSaveHistory((history) => [...history, previous].slice(-10))
      setAutoConfigNotice(null)
      await mutate(normalized, false)
      setSaveState('saved')
    } catch {
      setSaveState('cached')
    }
  }

  // Undo: discard unsaved changes, or (with none) restore the rules from before the last save.
  async function handleUndo() {
    if (hasChanges) {
      setDraftRules(draftBase)
      setSaveState('idle')
      setAutoConfigNotice(null)
      return
    }
    const previous = saveHistory[saveHistory.length - 1]
    if (!previous) return
    if (typeof window !== 'undefined' && !window.confirm('Restore the bot rules from before your last save?')) return
    // Assets that the undone save added go back to the default rule (bot off).
    const restoredRules = { ...previous.assetRules }
    Object.keys(draftBase?.assetRules || {}).forEach((asset) => {
      if (asset !== DEFAULT_ASSET && !restoredRules[asset]) restoredRules[asset] = ensureAssetRule({ assetRules: {} }, asset)
    })
    setSaveHistory((history) => history.slice(0, -1))
    await saveRules(
      { ...previous, assetRules: restoredRules, activePositions: rules.activePositions, executionLogs: rules.executionLogs },
      { recordHistory: false },
    )
  }

  function updateRoot(key, value) {
    setSaveState('idle')
    setDraftRules((prev) => normalizeRules({ ...prev, [key]: value }))
  }

  function updateAssetRule(key, value) {
    setSaveState('idle')
    setDraftRules((prev) => {
      const base = normalizeRules(prev)
      return {
        ...base,
        assetRules: {
          ...base.assetRules,
          [selectedAsset]: {
            ...ensureAssetRule(base, selectedAsset),
            [key]: value,
          },
        },
      }
    })
  }

  async function handleManualSave() {
    await saveRules({
      ...draftRules,
      selectedAsset,
      activePositions: rules.activePositions,
      executionLogs: rules.executionLogs,
    })
  }

  async function runManualExecution(side = 'BUY') {
    const normalizedSide = String(side || 'BUY').toUpperCase() === 'SELL' ? 'SELL' : 'BUY'
    if (!selectedAsset || selectedAsset === DEFAULT_ASSET) {
      setRunState('error')
      setRunMessage('Choose a watchlist asset first.')
      return
    }

    setRunState('running')
    setRunMessage(`Running ${normalizedSide} test...`)

    try {
      let marketPrice = null
      try {
        const stats = await apiFetch(`/api/market/stats/${encodeURIComponent(selectedAsset)}`)
        marketPrice = Number(stats?.latest_price)
      } catch {
        marketPrice = null
      }

      const maxCap = Number(assetRule.maxCapitalPerTrade)
      const requestedAmount = Number.isFinite(maxCap) && maxCap > 0 ? maxCap : 50

      const result = await apiFetch('/api/bot/signals/evaluate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          asset_symbol: selectedAsset,
          signal_received: `MANUAL_TEST_${normalizedSide}`,
          side: normalizedSide,
          requested_amount: requestedAmount,
          requested_price: Number.isFinite(marketPrice) && marketPrice > 0 ? marketPrice : undefined,
          confidence: 0.65,
          estimated_pnl: 0,
          metadata: {
            source: 'automated-ui-manual-test',
            strategy: assetRule.strategy,
            requested_side: normalizedSide,
          },
        }),
      })

      await mutate()
      setTab(TABS.LOGS)

      if (result?.action_taken === 'Executed') {
        setRunState('ok')
        setRunMessage(`${normalizedSide} filled on Alpaca paper for ${selectedAsset} at ${formatCurrency(result?.execution_price)}.`)
      } else if (result?.action_taken === 'Pending') {
        setRunState('ok')
        setRunMessage(`${normalizedSide} order for ${selectedAsset} sent to Alpaca and waiting to fill (e.g. market closed). The bot books it once Alpaca fills it.`)
      } else {
        setRunState('error')
        setRunMessage(result?.reason || `${normalizedSide} rejected by risk controls. Check Execution Audit Logs for details.`)
      }
    } catch (error) {
      setRunState('error')
      setRunMessage(`Manual run failed: ${error.message}`)
    }
  }

  let statusLabel = 'Rules synced to database.'
  if (saveState === 'saving') statusLabel = 'Saving bot rules...'
  else if (saveState === 'cached') statusLabel = 'Save failed. Draft changes are still local.'
  else if (!data && rulesError) statusLabel = 'Could not load your saved rules. Refresh the page to try again.'
  else if (hasChanges) statusLabel = 'Unsaved changes in local draft.'
  else if (saveState === 'saved') statusLabel = 'Rules saved.'

  return (
    <AppShell
      title="Automated Trading Setup"
      subtitle="Professional bot controls and execution oversight"
    >
      <div className={styles.layout}>
        <div className={styles.mainGrid}>
          {autoConfigNotice ? (
            <div className={styles.autoNotice} role="status">
              <div className={styles.autoNoticeBody}>
                <strong>Set up from AI Insights for {autoConfigNotice.asset}</strong>
                <span>
                  {autoConfigNotice.rule.strategy} · stop-loss {autoConfigNotice.rule.stopLossPct}% · trailing{' '}
                  {autoConfigNotice.rule.trailingStopLossPct}% · take-profit {autoConfigNotice.rule.takeProfitPct}% · bot on.
                  Nothing is saved yet: review it below, then press <b>Save Rules</b>, or <b>Undo</b> to discard it.
                </span>
                <ul>
                  {(autoConfigNotice.reasons || []).map((reason) => <li key={reason}>{reason}</li>)}
                </ul>
              </div>
              <button type="button" className={styles.autoNoticeClose} onClick={() => setAutoConfigNotice(null)} aria-label="Dismiss">×</button>
            </div>
          ) : null}
          <section className={styles.stepCard} aria-label="Asset selection and trading mode">
            <div className={styles.row}>
              <span className={styles.stepNum}>1</span>
              <h2 className={styles.stepTitle}>Asset Selection & Trading Mode</h2>
            </div>
            <div className={styles.fieldGrid}>
              <div className={styles.field}>
                <div className={styles.label}>Asset from Watchlist</div>
                <select
                  className={styles.select}
                  value={selectedAsset}
                  onChange={(event) => selectAsset(event.target.value)}
                  aria-label="Select watchlist asset"
                >
                  {assetOptions.map((symbol) => (
                    <option key={symbol} value={symbol}>
                      {symbol === DEFAULT_ASSET ? 'No Watchlist Asset' : symbol}
                    </option>
                  ))}
                </select>
              </div>

              <div className={styles.togglePanel}>
                <div className={styles.toggleRow}>
                  <div>
                    <div className={styles.k}>Bot for {selectedAsset === DEFAULT_ASSET ? 'this asset' : selectedAsset}</div>
                    <div className={styles.v}>{assetRule.isActive ? 'Bot Active' : 'Bot Inactive'}</div>
                  </div>
                  <button
                    type="button"
                    className={`${styles.switch} ${assetRule.isActive ? styles.switchOn : ''}`}
                    onClick={() => updateAssetRule('isActive', !assetRule.isActive)}
                    disabled={selectedAsset === DEFAULT_ASSET}
                    aria-label={`Toggle bot for ${selectedAsset}`}
                    aria-pressed={Boolean(assetRule.isActive)}
                  >
                    <span className={styles.knob} />
                  </button>
                </div>

                <div className={styles.toggleRow}>
                  <div>
                    <div className={styles.k}>Execution Mode (all assets)</div>
                    <div className={styles.v}>
                      {draftRules.executionMode === 'live' ? 'Live (not enabled, switch off)' : 'Alpaca Paper Trading'}
                    </div>
                  </div>
                  {/* Bot orders go to the Alpaca paper account; live (real-money) trading is not wired up,
                      so the switch can only turn a previously saved "live" back to paper. */}
                  <button
                    type="button"
                    className={`${styles.switch} ${draftRules.executionMode === 'live' ? styles.switchOn : ''}`}
                    onClick={() => updateRoot('executionMode', 'paper')}
                    disabled={draftRules.executionMode !== 'live'}
                    title="Orders are placed on your Alpaca paper account. Live trading is not enabled."
                    aria-label="Execution mode: Alpaca paper trading"
                    aria-pressed={draftRules.executionMode === 'live'}
                  >
                    <span className={styles.knob} />
                  </button>
                </div>
              </div>
            </div>
          </section>

          <section className={styles.stepCard} aria-label="Advanced risk management panel">
            <div className={styles.row}>
              <span className={styles.stepNum}>2</span>
              <h2 className={styles.stepTitle}>Advanced Risk Management</h2>
            </div>

            <div className={styles.field}>
              <FieldLabel
                tipId="tip-strategy"
                wideTip
                tip={
                  <>
                    <strong className={styles.tooltipTitle}>Trading strategies</strong>
                    {STRATEGIES.map((strategy) => (
                      <span
                        key={strategy}
                        className={`${styles.tooltipItem} ${strategy === assetRule.strategy ? styles.tooltipItemActive : ''}`}
                      >
                        <strong>{strategy}</strong>
                        {STRATEGY_INFO[strategy]}
                        {/* Full rules only for the selected strategy keeps the tooltip short. */}
                        {strategy === assetRule.strategy ? (
                          <span className={styles.tooltipRules}>
                            <span>BUY: {STRATEGY_RULES[strategy].buy}.</span>
                            <span>SELL: {STRATEGY_RULES[strategy].sell}.</span>
                          </span>
                        ) : null}
                      </span>
                    ))}
                    <span className={styles.tooltipFoot}>Full rules for your pick are under “When the bot trades” below.</span>
                  </>
                }
              >
                Strategy
              </FieldLabel>
              <select
                className={styles.select}
                value={assetRule.strategy}
                onChange={(event) => updateAssetRule('strategy', event.target.value)}
                aria-label="Select strategy"
              >
                {STRATEGIES.map((strategy) => (
                  <option key={strategy} value={strategy}>{strategy}</option>
                ))}
              </select>
            </div>

            {assetRule.strategy === DCA_STRATEGY ? (
              <div className={styles.field}>
                <FieldLabel
                  tipId="tip-dca-interval"
                  tip="How often the bot buys. Each buy spends your Max Capital Allocation per Trade, whatever the AI signals say."
                >
                  Buy Frequency
                </FieldLabel>
                <select
                  className={styles.select}
                  value={Number(assetRule.dcaIntervalHours) || 24}
                  onChange={(event) => updateAssetRule('dcaIntervalHours', Number(event.target.value))}
                  aria-label="DCA buy frequency"
                >
                  {DCA_INTERVALS.map((item) => (
                    <option key={item.hours} value={item.hours}>{item.label}</option>
                  ))}
                </select>
              </div>
            ) : null}

            <div className={styles.field}>
              <FieldLabel tipId="tip-stop-loss" tip={<RangeTip text={RISK_INFO.stopLoss} limits={PCT_LIMITS.stopLossPct} offText="no loss limit" />}>Absolute Stop-Loss (%)</FieldLabel>
              <PercentField
                {...PCT_LIMITS.stopLossPct}
                value={assetRule.stopLossPct}
                onChange={(value) => updateAssetRule('stopLossPct', value)}
                label="Absolute stop-loss percentage"
              />
            </div>

            <div className={styles.field}>
              <FieldLabel tipId="tip-trailing-stop" tip={<RangeTip text={RISK_INFO.trailingStop} limits={PCT_LIMITS.trailingStopLossPct} offText="no trailing stop" />}>Trailing Stop-Loss (%)</FieldLabel>
              <PercentField
                {...PCT_LIMITS.trailingStopLossPct}
                value={assetRule.trailingStopLossPct}
                onChange={(value) => updateAssetRule('trailingStopLossPct', value)}
                label="Trailing stop-loss percentage"
              />
            </div>

            <div className={styles.field}>
              <FieldLabel tipId="tip-take-profit" tip={<RangeTip text={RISK_INFO.takeProfit} limits={PCT_LIMITS.takeProfitPct} offText="no profit target (hold long-term)" />}>Take-Profit Target (%)</FieldLabel>
              <PercentField
                {...PCT_LIMITS.takeProfitPct}
                value={assetRule.takeProfitPct}
                onChange={(value) => updateAssetRule('takeProfitPct', value)}
                label="Take-profit target percentage"
              />
            </div>

            <div className={styles.fieldGrid}>
              <div className={styles.field}>
                <FieldLabel tipId="tip-max-capital" tip={RISK_INFO.maxCapital}>Max Capital Allocation per Trade ($)</FieldLabel>
                <input className={styles.input} type="number" min={10} step={10} value={assetRule.maxCapitalPerTrade} onChange={(event) => updateAssetRule('maxCapitalPerTrade', Number(event.target.value))} aria-label="Max capital per trade" />
              </div>
              <div className={styles.field}>
                <FieldLabel tipId="tip-max-daily-loss" tip={RISK_INFO.maxDailyLoss}>Max Daily Loss Limit ($)</FieldLabel>
                <input className={styles.input} type="number" min={10} step={10} value={assetRule.maxDailyLossLimit} onChange={(event) => updateAssetRule('maxDailyLossLimit', Number(event.target.value))} aria-label="Max daily loss limit" />
              </div>
            </div>

            <BotBehaviorPreview
              asset={selectedAsset}
              rule={assetRule}
              active={Boolean(assetRule.isActive) && selectedAsset !== DEFAULT_ASSET}
              unsaved={hasChanges}
              position={activePositions[0]}
            />
          </section>

          <section className={styles.stepCard} aria-label="Active positions and execution logs">
            <div className={styles.rowBetween}>
              <div className={styles.row}>
                <span className={styles.stepNum}>3</span>
                <h2 className={styles.stepTitle}>Active Positions & Execution Logs</h2>
              </div>
              <div className={styles.actionsRow}>
                <button
                  type="button"
                  className={styles.runBtn}
                  onClick={() => runManualExecution('BUY')}
                  disabled={runState === 'running' || isLoading}
                >
                  {runState === 'running' ? 'Running...' : 'Run Bot Now'}
                </button>
                <button
                  type="button"
                  className={`${styles.runBtn} ${styles.sellBtn}`}
                  onClick={() => runManualExecution('SELL')}
                  disabled={runState === 'running' || isLoading}
                >
                  {runState === 'running' ? 'Running...' : 'Sell Test'}
                </button>
                <div className={styles.tabRow}>
                <button type="button" className={`${styles.tabBtn} ${tab === TABS.POSITIONS ? styles.tabBtnActive : ''}`} onClick={() => setTab(TABS.POSITIONS)}>Active Bot Positions</button>
                <button type="button" className={`${styles.tabBtn} ${tab === TABS.LOGS ? styles.tabBtnActive : ''}`} onClick={() => setTab(TABS.LOGS)}>Execution Audit Logs</button>
                </div>
              </div>
            </div>

            {runMessage ? (
              <p className={`${styles.runMessage} ${runState === 'ok' ? styles.runOk : styles.runError}`}>
                {runMessage}
              </p>
            ) : null}
            {!assetRule.isActive ? <p className={styles.hint}>Bot is off for {selectedAsset === DEFAULT_ASSET ? 'this asset' : selectedAsset}. Any run will be logged as rejected until you turn it on in step 1 and save.</p> : null}

            {tab === TABS.POSITIONS ? (
              <div className={styles.tableWrap}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th>Asset</th>
                      <th>Entry Price</th>
                      <th>Current Price</th>
                      <th>Unrealized P/L</th>
                      <th>Trailing Stop Level</th>
                    </tr>
                  </thead>
                  <tbody>
                    {activePositions.map((row, index) => (
                      <tr key={`${row.asset}-${index}`}>
                        <td><strong>{row.asset}</strong></td>
                        <td>{formatCurrency(row.entryPrice)}</td>
                        <td>{formatCurrency(row.currentPrice)}</td>
                        <td className={Number(row.unrealizedPl) >= 0 ? styles.pos : styles.neg}>{formatSignedCurrency(row.unrealizedPl)}</td>
                        <td>{formatCurrency(row.trailingStopLevel)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!activePositions.length ? <p className={styles.emptyText}>No active bot positions for this asset.</p> : null}
              </div>
            ) : (
              <div className={styles.tableWrap}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th>Timestamp</th>
                      <th>Asset</th>
                      <th>AI Signal</th>
                      <th>Action Taken</th>
                      <th>Slippage/Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {executionLogs.map((row, index) => (
                      <tr key={`${row.timestamp}-${index}`}>
                        <td>{row.timestamp ? new Date(row.timestamp).toLocaleString() : '-'}</td>
                        <td><strong>{row.asset}</strong></td>
                        <td>{row.aiSignal}</td>
                        <td>{row.actionTaken}</td>
                        <td>{row.slippageReason || '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!executionLogs.length ? <p className={styles.emptyText}>No execution audit logs for this asset.</p> : null}
              </div>
            )}
          </section>
        </div>

        <aside className={styles.summary} aria-label="Summary">
          <p className={styles.summaryTitle}>Bot Control Center</p>
          <div className={styles.summaryGrid}>
            <div className={styles.kv}>
              <div className={styles.k}>Selected Asset</div>
              <div className={styles.v}>{selectedAsset === DEFAULT_ASSET ? 'None' : selectedAsset}</div>
            </div>
            <div className={styles.kv}>
              <div className={styles.k}>Bot Status</div>
              <div className={`${styles.v} ${assetRule.isActive ? styles.pos : ''}`}>{assetRule.isActive ? 'Active' : 'Inactive'}</div>
              <div className={styles.summaryNote}>
                Active on {Object.entries(draftRules.assetRules || {}).filter(([symbol, rule]) => symbol !== DEFAULT_ASSET && rule?.isActive).length} asset(s)
              </div>
            </div>
            <div className={styles.kv}>
              <div className={styles.k}>Strategy</div>
              <div className={styles.v}>{assetRule.strategy}</div>
            </div>
            <div className={styles.kv}>
              <div className={styles.k}>Stop / Trailing / TP</div>
              <div className={styles.v}>{formatExitPct(assetRule.stopLossPct)} / {formatExitPct(assetRule.trailingStopLossPct)} / {formatExitPct(assetRule.takeProfitPct)}</div>
            </div>
            <div className={styles.kv}>
              <div className={styles.k}>Capital Guardrails</div>
              <div className={styles.v}>{formatCurrency(assetRule.maxCapitalPerTrade)} / {formatCurrency(assetRule.maxDailyLossLimit)}</div>
            </div>
          </div>
          <div className={styles.saveRow}>
            <button
              type="button"
              className={styles.saveBtn}
              onClick={handleManualSave}
              disabled={saveState === 'saving' || !hasChanges}
            >
              {saveState === 'saving' ? 'Saving...' : 'Save Rules'}
            </button>
            <button
              type="button"
              className={styles.undoBtn}
              onClick={handleUndo}
              disabled={saveState === 'saving' || (!hasChanges && !saveHistory.length)}
              title={hasChanges ? 'Discard unsaved changes' : saveHistory.length ? 'Restore the rules from before your last save' : 'Nothing to undo'}
            >
              ↶ {hasChanges ? 'Undo changes' : saveHistory.length ? 'Undo last save' : 'Undo'}
            </button>
          </div>
          <div className={styles.summaryStatus}>{isLoading ? 'Loading rules...' : statusLabel}</div>
          <div className={styles.summaryStatus}>Auto-refresh: every 15s for positions/logs</div>
        </aside>
      </div>
    </AppShell>
  )
}
