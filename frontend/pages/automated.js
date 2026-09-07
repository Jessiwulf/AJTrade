import { useMemo, useState } from 'react'
import useSWR from 'swr'
import AppShell from '../components/AppShell'
import { apiFetch } from '../lib/api'
import styles from '../styles/Automated.module.css'

const DEFAULT_ASSET = 'NO_ASSET'

const DEFAULT_RULESET = {
  botActive: false,
  executionMode: 'paper',
  selectedAsset: DEFAULT_ASSET,
  updatedAt: null,
  assetRules: {
    [DEFAULT_ASSET]: {
      strategy: 'Trend Following',
      stopLossPct: 2,
      trailingStopLossPct: 1.2,
      takeProfitPct: 5,
      maxCapitalPerTrade: 1000,
      maxDailyLossLimit: 500,
    },
  },
  activePositions: [],
  executionLogs: [],
}

const STRATEGIES = ['Trend Following', 'Mean Reversion', 'News Momentum', 'AI Momentum + Sentiment']
const TABS = {
  POSITIONS: 'positions',
  LOGS: 'logs',
}

function formatCurrency(value) {
  const amount = Number(value)
  if (!Number.isFinite(amount)) return '$0.00'
  return amount.toLocaleString(undefined, {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
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
    strategy: 'Trend Following',
    stopLossPct: 2,
    trailingStopLossPct: 1.2,
    takeProfitPct: 5,
    maxCapitalPerTrade: 1000,
    maxDailyLossLimit: 500,
  }
}

async function fetchBotRules() {
  try {
    const data = await apiFetch('/api/bot/rules')
    return normalizeRules(data)
  } catch {
    return DEFAULT_RULESET
  }
}

export default function Automated() {
  const [tab, setTab] = useState(TABS.POSITIONS)
  const [saveState, setSaveState] = useState('idle')

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

  const { data, mutate, isLoading } = useSWR('bot-rules', fetchBotRules, {
    dedupingInterval: 5 * 60 * 1000,
    revalidateOnFocus: false,
    revalidateOnReconnect: false,
    revalidateIfStale: false,
    shouldRetryOnError: false,
    keepPreviousData: true,
  })

  const rules = useMemo(() => normalizeRules(data), [data])
  const selectedAsset = rules.selectedAsset && rules.selectedAsset !== DEFAULT_ASSET
    ? rules.selectedAsset
    : (watchlistSymbols[0] || DEFAULT_ASSET)

  const assetRule = useMemo(() => ensureAssetRule(rules, selectedAsset), [rules, selectedAsset])

  const activePositions = useMemo(
    () => (rules.activePositions || []).filter((position) => selectedAsset === DEFAULT_ASSET || position.asset === selectedAsset),
    [rules, selectedAsset],
  )

  const executionLogs = useMemo(
    () => (rules.executionLogs || []).filter((log) => selectedAsset === DEFAULT_ASSET || log.asset === selectedAsset),
    [rules, selectedAsset],
  )

  async function saveRules(nextRules) {
    const payload = normalizeRules(nextRules)
    setSaveState('saving')
    await mutate(payload, false)
    try {
      const server = await apiFetch('/api/bot/rules', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...payload, updatedAt: new Date().toISOString() }),
      })
      await mutate(normalizeRules(server || payload), false)
      setSaveState('saved')
    } catch {
      setSaveState('cached')
    }
  }

  function updateRoot(key, value) {
    saveRules({
      ...rules,
      selectedAsset,
      [key]: value,
    })
  }

  function updateAssetRule(key, value) {
    saveRules({
      ...rules,
      selectedAsset,
      assetRules: {
        ...rules.assetRules,
        [selectedAsset]: {
          ...ensureAssetRule(rules, selectedAsset),
          [key]: value,
        },
      },
    })
  }

  const statusLabel = saveState === 'saving'
    ? 'Saving bot rules...'
    : (saveState === 'saved' ? 'Rules synced to /api/bot/rules' : (saveState === 'cached' ? 'Offline mode: rules cached locally via SWR' : ''))

  return (
    <AppShell
      title="Automated Trading Setup"
      subtitle="Professional bot controls and execution oversight"
    >
      <div className={styles.layout}>
        <div className={styles.mainGrid}>
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
                  onChange={(event) => updateRoot('selectedAsset', event.target.value)}
                  aria-label="Select watchlist asset"
                >
                  {(watchlistSymbols.length ? watchlistSymbols : [DEFAULT_ASSET]).map((symbol) => (
                    <option key={symbol} value={symbol}>
                      {symbol === DEFAULT_ASSET ? 'No Watchlist Asset' : symbol}
                    </option>
                  ))}
                </select>
              </div>

              <div className={styles.togglePanel}>
                <div className={styles.toggleRow}>
                  <div>
                    <div className={styles.k}>Master Toggle</div>
                    <div className={styles.v}>{rules.botActive ? 'Bot Active' : 'Bot Inactive'}</div>
                  </div>
                  <button
                    type="button"
                    className={`${styles.switch} ${rules.botActive ? styles.switchOn : ''}`}
                    onClick={() => updateRoot('botActive', !rules.botActive)}
                    aria-label="Toggle bot active state"
                    aria-pressed={Boolean(rules.botActive)}
                  >
                    <span className={styles.knob} />
                  </button>
                </div>

                <div className={styles.toggleRow}>
                  <div>
                    <div className={styles.k}>Execution Mode</div>
                    <div className={styles.v}>{rules.executionMode === 'live' ? 'Live Execution' : 'Paper Trading'}</div>
                  </div>
                  <button
                    type="button"
                    className={`${styles.switch} ${rules.executionMode === 'live' ? styles.switchOn : ''}`}
                    onClick={() => updateRoot('executionMode', rules.executionMode === 'live' ? 'paper' : 'live')}
                    aria-label="Toggle paper trading or live execution"
                    aria-pressed={rules.executionMode === 'live'}
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
              <div className={styles.label}>Strategy</div>
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

            <div className={styles.field}>
              <div className={styles.label}>Absolute Stop-Loss (%)</div>
              <div className={styles.sliderRow}>
                <input className={styles.range} type="range" min={0.2} max={20} step={0.1} value={assetRule.stopLossPct} onChange={(event) => updateAssetRule('stopLossPct', Number(event.target.value))} aria-label="Absolute stop-loss percentage" />
                <div className={styles.pill}>{Number(assetRule.stopLossPct).toFixed(1)}%</div>
              </div>
            </div>

            <div className={styles.field}>
              <div className={styles.label}>Trailing Stop-Loss (%)</div>
              <div className={styles.sliderRow}>
                <input className={styles.range} type="range" min={0.1} max={15} step={0.1} value={assetRule.trailingStopLossPct} onChange={(event) => updateAssetRule('trailingStopLossPct', Number(event.target.value))} aria-label="Trailing stop-loss percentage" />
                <div className={styles.pill}>{Number(assetRule.trailingStopLossPct).toFixed(1)}%</div>
              </div>
            </div>

            <div className={styles.field}>
              <div className={styles.label}>Take-Profit Target (%)</div>
              <div className={styles.sliderRow}>
                <input className={styles.range} type="range" min={0.5} max={30} step={0.1} value={assetRule.takeProfitPct} onChange={(event) => updateAssetRule('takeProfitPct', Number(event.target.value))} aria-label="Take-profit target percentage" />
                <div className={styles.pill}>{Number(assetRule.takeProfitPct).toFixed(1)}%</div>
              </div>
            </div>

            <div className={styles.fieldGrid}>
              <div className={styles.field}>
                <div className={styles.label}>Max Capital Allocation per Trade ($)</div>
                <input className={styles.input} type="number" min={10} step={10} value={assetRule.maxCapitalPerTrade} onChange={(event) => updateAssetRule('maxCapitalPerTrade', Number(event.target.value))} aria-label="Max capital per trade" />
              </div>
              <div className={styles.field}>
                <div className={styles.label}>Max Daily Loss Limit ($)</div>
                <input className={styles.input} type="number" min={10} step={10} value={assetRule.maxDailyLossLimit} onChange={(event) => updateAssetRule('maxDailyLossLimit', Number(event.target.value))} aria-label="Max daily loss limit" />
              </div>
            </div>
          </section>

          <section className={styles.stepCard} aria-label="Active positions and execution logs">
            <div className={styles.rowBetween}>
              <div className={styles.row}>
                <span className={styles.stepNum}>3</span>
                <h2 className={styles.stepTitle}>Active Positions & Execution Logs</h2>
              </div>
              <div className={styles.tabRow}>
                <button type="button" className={`${styles.tabBtn} ${tab === TABS.POSITIONS ? styles.tabBtnActive : ''}`} onClick={() => setTab(TABS.POSITIONS)}>Active Bot Positions</button>
                <button type="button" className={`${styles.tabBtn} ${tab === TABS.LOGS ? styles.tabBtnActive : ''}`} onClick={() => setTab(TABS.LOGS)}>Execution Audit Logs</button>
              </div>
            </div>

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
              <div className={styles.k}>Strategy</div>
              <div className={styles.v}>{assetRule.strategy}</div>
            </div>
            <div className={styles.kv}>
              <div className={styles.k}>Stop / Trailing / TP</div>
              <div className={styles.v}>{assetRule.stopLossPct}% / {assetRule.trailingStopLossPct}% / {assetRule.takeProfitPct}%</div>
            </div>
            <div className={styles.kv}>
              <div className={styles.k}>Capital Guardrails</div>
              <div className={styles.v}>{formatCurrency(assetRule.maxCapitalPerTrade)} / {formatCurrency(assetRule.maxDailyLossLimit)}</div>
            </div>
          </div>
          <div className={styles.summaryStatus}>{isLoading ? 'Loading rules...' : statusLabel}</div>
        </aside>
      </div>
    </AppShell>
  )
}
