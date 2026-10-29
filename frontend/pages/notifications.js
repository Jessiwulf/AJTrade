import { useCallback, useEffect, useMemo, useState } from 'react'
import AppShell from '../components/AppShell'
import { apiFetch } from '../lib/api'
import styles from '../styles/Notifications.module.css'

const MODES = [
  { key: 'off', label: 'Off', description: 'No alerts are sent.' },
  { key: 'email', label: 'Email', description: 'Alerts go to your account email.' },
  { key: 'discord', label: 'Discord', description: 'Alerts go to your Discord channel only.' },
  { key: 'both', label: 'Email + Discord', description: 'Every alert goes to both channels.' },
]

const CATEGORY_TITLES = {
  trading: 'Trading executions',
  market: 'Market risk',
}

const WEBHOOK_PATTERN = /^https:\/\/(?:(?:ptb|canary)\.)?discord(?:app)?\.com\/api\/webhooks\/\d{5,30}\/[A-Za-z0-9._-]{20,200}$/

const CHANNEL_LABELS = { email: 'Email', discord: 'Discord' }

function modeToChannels(mode) {
  return {
    email_enabled: mode === 'email' || mode === 'both',
    discord_enabled: mode === 'discord' || mode === 'both',
  }
}

function formatTime(value) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

function Switch({ checked, onChange, label, disabled }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      className={`${styles.switch} ${checked ? styles.switchOn : ''}`}
      onClick={() => onChange(!checked)}
      disabled={disabled}
    >
      <span className={styles.switchThumb} />
    </button>
  )
}

function SeverityBadge({ severity }) {
  if (severity === 'critical') return <span className={`${styles.badge} ${styles.badgeCritical}`}>Critical</span>
  if (severity === 'warning') return <span className={`${styles.badge} ${styles.badgeWarning}`}>Warning</span>
  return null
}

function ChannelResult({ channel, result }) {
  const ok = result?.status === 'sent'
  return (
    <span
      className={`${styles.chip} ${ok ? styles.chipOk : styles.chipFail}`}
      title={result?.detail || (ok ? 'Delivered' : result?.status)}
    >
      {CHANNEL_LABELS[channel] || channel} {ok ? '✓' : '✕'}
    </span>
  )
}

export default function NotificationsPage() {
  const [saved, setSaved] = useState(null)
  const [mode, setMode] = useState('email')
  const [events, setEvents] = useState({})
  const [webhookInput, setWebhookInput] = useState('')
  const [editingWebhook, setEditingWebhook] = useState(false)
  const [removeWebhook, setRemoveWebhook] = useState(false)
  const [history, setHistory] = useState([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [message, setMessage] = useState(null)

  const applyServerState = useCallback((data) => {
    setSaved(data)
    setMode(data.mode)
    setEvents(data.events || {})
    setWebhookInput('')
    setEditingWebhook(false)
    setRemoveWebhook(false)
  }, [])

  const loadHistory = useCallback(async () => {
    try {
      const data = await apiFetch('/api/notifications/history?limit=25')
      setHistory(Array.isArray(data?.items) ? data.items : [])
    } catch {
      // History is secondary; keep the last list.
    }
  }, [])

  useEffect(() => {
    let active = true
    ;(async () => {
      try {
        const data = await apiFetch('/api/notifications/preferences')
        if (active) applyServerState(data)
      } catch (e) {
        if (active) setMessage({ type: 'error', text: `Could not load notification settings: ${e.message}` })
      } finally {
        if (active) setLoading(false)
      }
    })()
    loadHistory()
    const id = setInterval(loadHistory, 30000)
    return () => {
      active = false
      clearInterval(id)
    }
  }, [applyServerState, loadHistory])

  const catalogByCategory = useMemo(() => {
    const groups = {}
    for (const item of saved?.catalog || []) {
      ;(groups[item.category] = groups[item.category] || []).push(item)
    }
    return groups
  }, [saved])

  const webhookConfigured = Boolean(saved?.discord_webhook?.configured) && !removeWebhook
  const webhookDraft = webhookInput.trim()
  const webhookDraftValid = !webhookDraft || WEBHOOK_PATTERN.test(webhookDraft)
  const wantsDiscord = mode === 'discord' || mode === 'both'
  const hasWebhookAfterSave = (webhookConfigured || Boolean(webhookDraft)) && webhookDraftValid
  const wantsEmail = mode === 'email' || mode === 'both'

  const dirty = useMemo(() => {
    if (!saved) return false
    if (mode !== saved.mode) return true
    if (webhookDraft || removeWebhook) return true
    return Object.keys(events).some((key) => events[key] !== saved.events?.[key])
  }, [saved, mode, events, webhookDraft, removeWebhook])

  const validationError = useMemo(() => {
    if (!webhookDraftValid) return 'That is not a Discord webhook URL. It should look like https://discord.com/api/webhooks/…'
    if (wantsDiscord && !hasWebhookAfterSave) return 'Add your Discord webhook URL to use Discord alerts.'
    return null
  }, [webhookDraftValid, wantsDiscord, hasWebhookAfterSave])

  async function save() {
    if (validationError) {
      setMessage({ type: 'error', text: validationError })
      return
    }
    setSaving(true)
    setMessage(null)
    try {
      const body = { ...modeToChannels(mode), events }
      if (webhookDraft) body.discord_webhook_url = webhookDraft
      else if (removeWebhook) body.discord_webhook_url = ''
      const data = await apiFetch('/api/notifications/preferences', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      applyServerState(data)
      setMessage({ type: 'success', text: 'Notification settings saved.' })
    } catch (e) {
      setMessage({ type: 'error', text: e.message })
    } finally {
      setSaving(false)
    }
  }

  function discardChanges() {
    if (saved) applyServerState(saved)
    setMessage(null)
  }

  async function sendTest() {
    setTesting(true)
    setMessage(null)
    try {
      const result = await apiFetch('/api/notifications/test', { method: 'POST' })
      const channels = result?.channels || {}
      const parts = Object.entries(channels).map(([channel, r]) => (
        r.status === 'sent' ? `${CHANNEL_LABELS[channel]}: delivered` : `${CHANNEL_LABELS[channel]}: failed (${r.detail || r.status})`
      ))
      const allOk = Object.values(channels).length > 0 && Object.values(channels).every((r) => r.status === 'sent')
      setMessage({ type: allOk ? 'success' : 'error', text: `Test alert. ${parts.join(' · ')}` })
    } catch (e) {
      setMessage({ type: 'error', text: e.message })
    } finally {
      setTesting(false)
    }
  }

  const modeInfo = MODES.find((m) => m.key === mode)

  return (
    <AppShell title="Notifications" subtitle="Real-time alerts for bot trades and market risk">
      <div className={styles.page}>
        {loading ? (
          <section className={styles.card}><p className={styles.muted}>Loading notification settings…</p></section>
        ) : !saved ? (
          <section className={styles.card}><p className={styles.errorText}>{message?.text || 'Notification settings are unavailable.'}</p></section>
        ) : (
          <>
            <div className={styles.grid}>
              <div className={styles.stack}>
                <section className={styles.card} aria-labelledby="delivery-title">
                  <h2 id="delivery-title" className={styles.cardTitle}>Delivery</h2>
                  <p className={styles.cardLead}>Choose where AJTrade sends alerts. You can turn everything off at any time.</p>
                  <div className={styles.segmented} role="radiogroup" aria-label="Alert delivery">
                    {MODES.map((m) => (
                      <button
                        key={m.key}
                        type="button"
                        role="radio"
                        aria-checked={mode === m.key}
                        className={`${styles.segment} ${mode === m.key ? styles.segmentActive : ''}`}
                        onClick={() => setMode(m.key)}
                      >
                        {m.label}
                      </button>
                    ))}
                  </div>
                  <p className={styles.muted}>{modeInfo?.description}</p>
                  {wantsEmail && !saved.email_delivery_available ? (
                    <p className={styles.warnText}>
                      Email delivery isn&apos;t configured on the server yet (SMTP). Email alerts will be logged as failed until it is set up.
                    </p>
                  ) : null}
                </section>

                <section className={styles.card} aria-labelledby="channels-title">
                  <h2 id="channels-title" className={styles.cardTitle}>Channels</h2>

                  <div className={`${styles.channel} ${wantsEmail ? '' : styles.channelInactive}`}>
                    <div className={styles.channelIcon} aria-hidden="true">@</div>
                    <div className={styles.channelBody}>
                      <div className={styles.channelHead}>
                        <strong>Email</strong>
                        <span className={styles.defaultTag}>Default</span>
                      </div>
                      <p className={styles.muted}>
                        {saved.email ? <>Sends to <strong>{saved.email}</strong> (your account email).</> : 'Your account email will be used.'}
                      </p>
                    </div>
                    <span className={`${styles.chip} ${wantsEmail ? styles.chipOk : styles.chipIdle}`}>{wantsEmail ? 'On' : 'Off'}</span>
                  </div>

                  <div className={`${styles.channel} ${wantsDiscord ? '' : styles.channelInactive}`}>
                    <div className={`${styles.channelIcon} ${styles.channelIconDiscord}`} aria-hidden="true">#</div>
                    <div className={styles.channelBody}>
                      <div className={styles.channelHead}>
                        <strong>Discord</strong>
                        <span className={styles.optionalTag}>Optional</span>
                      </div>
                      {webhookConfigured && !editingWebhook ? (
                        <div className={styles.webhookRow}>
                          <code className={styles.webhookHint}>{saved.discord_webhook.hint}</code>
                          <button type="button" className={styles.linkBtn} onClick={() => setEditingWebhook(true)}>Replace</button>
                          <button
                            type="button"
                            className={`${styles.linkBtn} ${styles.linkDanger}`}
                            onClick={() => {
                              setRemoveWebhook(true)
                              if (mode === 'discord') setMode('off')
                              if (mode === 'both') setMode('email')
                            }}
                          >
                            Remove
                          </button>
                        </div>
                      ) : (
                        <>
                          <input
                            className={`${styles.input} ${webhookDraftValid ? '' : styles.inputInvalid}`}
                            type="password"
                            autoComplete="off"
                            spellCheck={false}
                            value={webhookInput}
                            onChange={(e) => setWebhookInput(e.target.value)}
                            placeholder="https://discord.com/api/webhooks/…"
                            aria-label="Discord webhook URL"
                            aria-invalid={!webhookDraftValid}
                          />
                          <p className={styles.help}>
                            In Discord: Server Settings → Integrations → Webhooks → New Webhook → Copy Webhook URL.
                            The URL is stored encrypted and never shown again.
                          </p>
                          {removeWebhook ? (
                            <button type="button" className={styles.linkBtn} onClick={() => setRemoveWebhook(false)}>Undo remove</button>
                          ) : null}
                          {editingWebhook ? (
                            <button type="button" className={styles.linkBtn} onClick={() => { setEditingWebhook(false); setWebhookInput('') }}>Cancel</button>
                          ) : null}
                        </>
                      )}
                    </div>
                    <span className={`${styles.chip} ${wantsDiscord ? styles.chipOk : styles.chipIdle}`}>{wantsDiscord ? 'On' : 'Off'}</span>
                  </div>
                </section>

                <section className={styles.card} aria-labelledby="events-title">
                  <h2 id="events-title" className={styles.cardTitle}>Alert types</h2>
                  <p className={styles.cardLead}>Pick which events trigger an alert.{mode === 'off' ? ' Alerts are currently off, so nothing will be sent.' : ''}</p>
                  {Object.entries(catalogByCategory).map(([category, items]) => (
                    <div key={category} className={styles.eventGroup}>
                      <h3 className={styles.groupTitle}>{CATEGORY_TITLES[category] || category}</h3>
                      {items.map((item) => (
                        <div key={item.key} className={`${styles.eventRow} ${mode === 'off' ? styles.eventRowMuted : ''}`}>
                          <div className={styles.eventText}>
                            <div className={styles.eventHead}>
                              <strong>{item.label}</strong>
                              <SeverityBadge severity={item.severity} />
                            </div>
                            <p className={styles.muted}>{item.description}</p>
                          </div>
                          <Switch
                            checked={Boolean(events[item.key])}
                            onChange={(value) => setEvents((prev) => ({ ...prev, [item.key]: value }))}
                            label={item.label}
                          />
                        </div>
                      ))}
                    </div>
                  ))}
                </section>
              </div>

              <aside className={styles.stack}>
                <section className={`${styles.card} ${styles.actionsCard}`} aria-label="Save notification settings">
                  <h2 className={styles.cardTitle}>Status</h2>
                  <p className={styles.statusLine}>
                    <span className={`${styles.statusDot} ${saved.mode === 'off' ? styles.statusDotOff : ''}`} />
                    {saved.mode === 'off' ? 'Alerts are off' : `Alerts on · ${MODES.find((m) => m.key === saved.mode)?.label}`}
                  </p>
                  {validationError && dirty ? <p className={styles.errorText}>{validationError}</p> : null}
                  <div className={styles.actions}>
                    <button type="button" className={styles.primary} onClick={save} disabled={!dirty || saving || Boolean(validationError)}>
                      {saving ? 'Saving…' : 'Save changes'}
                    </button>
                    {dirty ? (
                      <button type="button" className={styles.secondary} onClick={discardChanges} disabled={saving}>Discard</button>
                    ) : null}
                  </div>
                  <button
                    type="button"
                    className={styles.secondary}
                    onClick={sendTest}
                    disabled={testing || dirty || saved.mode === 'off'}
                    title={dirty ? 'Save your changes first' : saved.mode === 'off' ? 'Turn on a channel first' : 'Send a test alert to your active channels'}
                  >
                    {testing ? 'Sending…' : 'Send test alert'}
                  </button>
                  {message ? (
                    <p className={message.type === 'error' ? styles.errorText : styles.successText} role="status">{message.text}</p>
                  ) : null}
                </section>

                <section className={styles.card} aria-labelledby="history-title">
                  <div className={styles.historyHead}>
                    <h2 id="history-title" className={styles.cardTitle}>Recent alerts</h2>
                    <button type="button" className={styles.linkBtn} onClick={loadHistory}>Refresh</button>
                  </div>
                  {history.length ? (
                    <ul className={styles.history}>
                      {history.map((item) => (
                        <li key={item.id} className={styles.historyItem}>
                          <span className={`${styles.sevDot} ${styles[`sev_${item.severity}`] || ''}`} aria-hidden="true" />
                          <div className={styles.historyBody}>
                            <strong className={styles.historyTitle}>{item.title}</strong>
                            <span className={styles.historyMeta}>{formatTime(item.created_at)}</span>
                            <div className={styles.chips}>
                              {Object.entries(item.channels || {}).map(([channel, result]) => (
                                <ChannelResult key={channel} channel={channel} result={result} />
                              ))}
                              {item.status === 'sending' ? <span className={`${styles.chip} ${styles.chipIdle}`}>Sending…</span> : null}
                            </div>
                          </div>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className={styles.muted}>No alerts yet. Bot trades and market risk events will appear here.</p>
                  )}
                </section>
              </aside>
            </div>
          </>
        )}
      </div>
    </AppShell>
  )
}
