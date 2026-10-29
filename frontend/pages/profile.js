import { useEffect, useMemo, useRef, useState } from 'react'
import AppShell from '../components/AppShell'
import Link from 'next/link'
import { useRouter } from 'next/router'
import { apiFetch, setAccessToken } from '../lib/api'
import { PROVIDER_META, useBroker } from '../lib/broker'
import { loadStoredProfile, saveStoredProfile } from '../lib/userProfile'
import styles from '../styles/Manage.module.css'
import ps from '../styles/Profile.module.css'

const AVATAR_PIXELS = 320
const DELETE_PHRASE = 'DELETE'

// PDPA right to erasure: removes the account and everything AJTrade stores about it.
function DeleteAccountCard() {
  const router = useRouter()
  const [open, setOpen] = useState(false)
  const [password, setPassword] = useState('')
  const [phrase, setPhrase] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const ready = password && phrase.trim().toUpperCase() === DELETE_PHRASE && !busy

  async function remove() {
    setBusy(true)
    setError('')
    try {
      await apiFetch('/api/profile/account', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password, confirm: phrase.trim().toUpperCase() }),
      })
      try {
        await apiFetch('/api/auth/logout', { method: 'POST' })
      } catch {
        // The session already ended with the account.
      }
      try {
        // Cached profile copies in this browser (name, avatar) go too.
        Object.keys(localStorage).filter((key) => key.startsWith('ajtrade')).forEach((key) => localStorage.removeItem(key))
      } catch {
        // ignore
      }
      setAccessToken(null)
      router.replace('/?account=deleted')
    } catch (err) {
      setError(err?.message || 'Could not delete the account.')
      setBusy(false)
    }
  }

  return (
    <section className={styles.card} aria-labelledby="delete-account-title">
      <p className={styles.cardTitle} id="delete-account-title">Delete account</p>
      <p className={ps.dangerText}>
        Permanently deletes your AJTrade account and all data stored with it: profile, watchlist, saved API keys, bot
        rules and logs, trade history, alerts and AI usage records. Your Alpaca or Settrade account itself is not closed
        and any open positions there stay as they are. This cannot be undone.{' '}
        <Link href="/privacy">Privacy Notice</Link>
      </p>
      {open ? (
        <form
          className={ps.dangerForm}
          onSubmit={(e) => {
            e.preventDefault()
            if (ready) remove()
          }}
        >
          <div className={styles.field}>
            <div className={styles.label}>Current Password</div>
            <input
              type="password"
              className={styles.input}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
          </div>
          <div className={styles.field}>
            <div className={styles.label}>Type {DELETE_PHRASE} to confirm</div>
            <input className={styles.input} value={phrase} onChange={(e) => setPhrase(e.target.value)} autoComplete="off" />
          </div>
          <div className={styles.rowWrap} style={{ marginTop: 14 }}>
            <button type="submit" className={styles.danger} disabled={!ready}>
              {busy ? 'Deleting...' : 'Delete my account permanently'}
            </button>
            <button type="button" className={styles.secondary} onClick={() => { setOpen(false); setError('') }} disabled={busy}>
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <div className={styles.rowWrap} style={{ marginTop: 12 }}>
          <button type="button" className={styles.danger} onClick={() => setOpen(true)}>Delete account...</button>
        </div>
      )}
      {error ? <p className={`${styles.msg} ${styles.msgError}`}>{error}</p> : null}
    </section>
  )
}
const MAX_UPLOAD_BYTES = 15 * 1024 * 1024

function initials(value) {
  const text = String(value || '').trim()
  if (!text) return 'U'
  const parts = text.split(/\s+/).filter(Boolean)
  const first = parts[0]?.[0] || ''
  const last = parts.length > 1 ? parts[parts.length - 1]?.[0] || '' : ''
  return `${first}${last}`.toUpperCase() || 'U'
}

// Center-crops to a square and downsizes, so a multi-MB phone photo becomes a ~30 KB avatar that
// fits in the profile record and in localStorage.
async function prepareAvatar(file) {
  if (!file?.type?.startsWith('image/')) throw new Error('Please choose an image file (JPG, PNG, WebP or GIF).')
  if (file.size > MAX_UPLOAD_BYTES) throw new Error('That image is larger than 15 MB. Please choose a smaller one.')
  const url = URL.createObjectURL(file)
  try {
    const img = await new Promise((resolve, reject) => {
      const image = new Image()
      image.onload = () => resolve(image)
      image.onerror = () => reject(new Error('This image could not be opened. Try a JPG or PNG.'))
      image.src = url
    })
    const side = Math.min(img.naturalWidth, img.naturalHeight)
    const size = Math.min(AVATAR_PIXELS, side)
    const canvas = document.createElement('canvas')
    canvas.width = size
    canvas.height = size
    const ctx = canvas.getContext('2d')
    ctx.fillStyle = '#ffffff' // transparent PNGs would otherwise turn black as JPEG
    ctx.fillRect(0, 0, size, size)
    ctx.imageSmoothingQuality = 'high'
    ctx.drawImage(img, (img.naturalWidth - side) / 2, (img.naturalHeight - side) / 2, side, side, 0, 0, size, size)
    return canvas.toDataURL('image/jpeg', 0.88)
  } finally {
    URL.revokeObjectURL(url)
  }
}

function CameraIcon({ size = 18 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 8h3l2-3h6l2 3h3a1 1 0 011 1v10a1 1 0 01-1 1H4a1 1 0 01-1-1V9a1 1 0 011-1z" />
      <circle cx="12" cy="13.5" r="3.5" />
    </svg>
  )
}

function AvatarPicker({ src, label, busy, onFile, inputRef }) {
  const [dragging, setDragging] = useState(false)

  function pick(files) {
    const file = files?.[0]
    if (file) onFile(file)
  }

  return (
    <div className={ps.avatarWrap}>
      <button
        type="button"
        className={`${ps.avatar} ${dragging ? ps.avatarDragging : ''} ${src ? '' : ps.avatarEmpty}`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragging(false)
          pick(e.dataTransfer?.files)
        }}
        disabled={busy}
        aria-label={src ? 'Change profile photo' : 'Upload a profile photo'}
      >
        <span className={ps.ring} aria-hidden="true" />
        <span className={ps.photo}>
          {src ? <img src={src} alt="" /> : <span className={ps.initials}>{initials(label)}</span>}
        </span>
        <span className={ps.overlay} aria-hidden="true">
          {busy ? <span className={ps.spinner} /> : <CameraIcon size={26} />}
          <span>{dragging ? 'Drop to upload' : busy ? 'Processing…' : src ? 'Change photo' : 'Add photo'}</span>
        </span>
        <span className={ps.badge} aria-hidden="true">
          <CameraIcon size={16} />
        </span>
      </button>
      <input
        ref={inputRef}
        type="file"
        accept="image/png,image/jpeg,image/webp,image/gif"
        className={ps.hiddenInput}
        onChange={(e) => {
          pick(e.target.files)
          e.target.value = '' // allow choosing the same file again
        }}
        tabIndex={-1}
        aria-hidden="true"
      />
    </div>
  )
}

const PROVIDER_ICONS = { alpaca: '$', settrade: '฿' }

function connectionText(status) {
  if (!status?.connected) return { text: 'Not connected', tone: 'off' }
  if (status.key === 'alpaca') return { text: 'Connected · Paper trading', tone: 'ok' }
  if (status.environment === 'production') return { text: 'Connected · Live account (read-only)', tone: 'warn' }
  if (!status.trading_enabled) return { text: 'Connected · Sandbox (add PIN to trade)', tone: 'warn' }
  return { text: 'Connected · Sandbox trading', tone: 'ok' }
}

function BrokerProviderCard() {
  const { provider, providers, loaded, setProvider } = useBroker()
  const [switching, setSwitching] = useState(null)
  const [notice, setNotice] = useState(null)

  async function choose(next) {
    if (next === provider || switching) return
    setSwitching(next)
    setNotice(null)
    try {
      const updated = await setProvider(next)
      const status = updated?.providers?.find((p) => p.key === next)
      setNotice({
        tone: status?.connected ? 'ok' : 'warn',
        text: status?.connected
          ? `Switched to ${PROVIDER_META[next].label}. Dashboard, Watchlist, Portfolio, Analytics and Auto Trade now show only ${PROVIDER_META[next].market} in ${PROVIDER_META[next].currency}.`
          : `Switched to ${PROVIDER_META[next].label}, but it isn't connected yet. Add your ${PROVIDER_META[next].label} keys in API Management to see your account.`,
      })
    } catch (e) {
      setNotice({ tone: 'error', text: `Could not switch provider: ${e.message}` })
    } finally {
      setSwitching(null)
    }
  }

  const statusByKey = Object.fromEntries(providers.map((p) => [p.key, p]))

  return (
    <section className={styles.card} aria-labelledby="broker-provider-title">
      <p className={styles.cardTitle} id="broker-provider-title">Trading provider</p>
      <p className={ps.providerLead}>
        Choose which brokerage AJTrade works with. Each page shows only the selected provider&apos;s account and assets, so US and Thai holdings are never mixed.
      </p>
      <div className={ps.providerGrid} role="radiogroup" aria-label="Trading provider">
        {Object.values(PROVIDER_META).map((meta) => {
          const active = provider === meta.key
          const status = connectionText(statusByKey[meta.key])
          return (
            <button
              key={meta.key}
              type="button"
              role="radio"
              aria-checked={active}
              className={`${ps.providerCard} ${active ? ps.providerActive : ''}`}
              onClick={() => choose(meta.key)}
              disabled={!loaded || Boolean(switching)}
            >
              <span className={ps.providerRadio} aria-hidden="true" />
              <span className={ps.providerFlag} aria-hidden="true">{PROVIDER_ICONS[meta.key]}</span>
              <span className={ps.providerBody}>
                <span className={ps.providerName}>{meta.label}</span>
                <span className={ps.providerMarket}>{meta.market} · {meta.currency}</span>
                <span className={`${ps.providerStatus} ${ps[`status_${status.tone}`] || ''}`}>
                  {switching === meta.key ? 'Switching…' : status.text}
                </span>
              </span>
            </button>
          )
        })}
      </div>
      {notice ? (
        <p className={`${ps.providerNotice} ${ps[`notice_${notice.tone}`] || ''}`} role="status">
          {notice.text}{' '}
          {notice.tone === 'warn' ? <Link href="/api-keys">Open API Management →</Link> : null}
        </p>
      ) : null}
    </section>
  )
}

export default function ProfilePage() {
  const [user, setUser] = useState(null)
  const [profile, setProfile] = useState({ full_name: '', avatar_url: '', email: '' })
  const [savedAvatar, setSavedAvatar] = useState('')
  const [avatarBusy, setAvatarBusy] = useState(false)
  const [avatarError, setAvatarError] = useState('')
  const fileInputRef = useRef(null)
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState(null)
  const [passwordForm, setPasswordForm] = useState({
    currentPassword: '',
    newPassword: '',
    confirmNewPassword: '',
  })
  const [passwordError, setPasswordError] = useState('')
  const [passwordMessage, setPasswordMessage] = useState('')

  async function handleAvatarFile(file) {
    setAvatarError('')
    setMessage(null)
    setAvatarBusy(true)
    try {
      const dataUrl = await prepareAvatar(file)
      setProfile((prev) => ({ ...prev, avatar_url: dataUrl }))
    } catch (e) {
      setAvatarError(e.message)
    } finally {
      setAvatarBusy(false)
    }
  }

  const messageClass = useMemo(() => {
    if (!message) return styles.msg
    if (String(message).toLowerCase().startsWith('error')) return `${styles.msg} ${styles.msgError}`
    return `${styles.msg} ${styles.msgSuccess}`
  }, [message])

  async function refresh() {
    setLoading(true)
    setMessage(null)
    try {
      const data = await apiFetch('/api/auth/me')
      setUser(data?.user || null)
      const stored = loadStoredProfile(data?.user)
      const avatarUrl = stored?.avatar_url || data?.profile?.avatar_url || ''
      setProfile({
        full_name: stored?.full_name || data?.profile?.full_name || '',
        avatar_url: avatarUrl,
        email: data?.profile?.email || data?.user?.email || '',
      })
      setSavedAvatar(avatarUrl)
      setAvatarError('')
    } catch (e) {
      setMessage(`Error: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  async function saveProfile() {
    const avatarValue = (profile.avatar_url || '').trim()
    if (avatarValue && !/^(https?:\/\/|data:image\/)/i.test(avatarValue)) {
      setMessage('Error: Profile picture link must be a web address starting with https:// (or upload a photo).')
      return
    }
    setLoading(true)
    setMessage(null)
    try {
      saveStoredProfile(user || { email: profile.email }, {
        full_name: profile.full_name,
        avatar_url: profile.avatar_url,
        email: profile.email,
      })

      const data = await apiFetch('/api/auth/profile', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          full_name: profile.full_name,
          avatar_url: profile.avatar_url,
        }),
      })
      setProfile({
        full_name: data?.full_name || '',
        avatar_url: data?.avatar_url || '',
        email: data?.email || profile.email || '',
      })
      setSavedAvatar(data?.avatar_url || '')
      setMessage('Profile saved')
    } catch (e) {
      setSavedAvatar(profile.avatar_url)
      setMessage(`Profile saved locally${e?.message ? ` (backend sync failed: ${e.message})` : ''}`)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  async function changePassword() {
    setPasswordError('')
    setPasswordMessage('')

    if (passwordForm.newPassword.length < 6) {
      setPasswordError('Password must be at least 6 characters.')
      return
    }
    if (passwordForm.newPassword !== passwordForm.confirmNewPassword) {
      setPasswordError('Passwords do not match')
      return
    }

    setLoading(true)
    try {
      const data = await apiFetch('/api/profile/password', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(passwordForm),
      })
      setPasswordForm({ currentPassword: '', newPassword: '', confirmNewPassword: '' })
      setPasswordMessage(data?.message || 'Password Changed Successfully.')
    } catch (e) {
      const msg = e?.message || 'Unable to change password.'
      if (msg === 'Current password is incorrect.') {
        setPasswordError('Current password is incorrect.')
      } else {
        setPasswordError(msg)
      }
    } finally {
      setLoading(false)
    }
  }

  const avatar = profile.avatar_url?.trim()
  const label = profile.full_name?.trim() || profile.email || 'User'
  const photoPending = (avatar || '') !== (savedAvatar?.trim() || '')
  const linkValue = avatar && !avatar.startsWith('data:') ? avatar : ''

  return (
    <AppShell title="Profile & Settings" subtitle="Update your username and profile picture">
      <div className={styles.page}>
        <section className={styles.card} aria-label="Profile editor">
          <p className={styles.cardTitle}>User Profile</p>
          <div className={ps.hero}>
            <AvatarPicker src={avatar} label={label} busy={avatarBusy} onFile={handleAvatarFile} inputRef={fileInputRef} />
            <div className={ps.identity}>
              <h2 className={ps.name}>{label}</h2>
              <p className={ps.email}>{profile.email || 'Signed in user'}</p>
              <div className={ps.photoActions}>
                <button
                  type="button"
                  className={ps.uploadBtn}
                  onClick={() => fileInputRef.current?.click()}
                  disabled={avatarBusy}
                >
                  <CameraIcon size={16} /> {avatar ? 'Change photo' : 'Upload photo'}
                </button>
                {avatar ? (
                  <button
                    type="button"
                    className={ps.removeBtn}
                    onClick={() => {
                      setProfile((prev) => ({ ...prev, avatar_url: '' }))
                      setAvatarError('')
                    }}
                    disabled={avatarBusy}
                  >
                    Remove
                  </button>
                ) : null}
              </div>
              <p className={ps.hint}>Click or drop an image on your photo. JPG, PNG or WebP; it&apos;s cropped to a square automatically.</p>
              {avatarError ? <p className={`${styles.msg} ${styles.msgError}`}>{avatarError}</p> : null}
              {photoPending && !avatarError ? (
                <p className={ps.pending}>{avatar ? 'New photo ready.' : 'Photo removed.'} Click <strong>Save profile</strong> to apply.</p>
              ) : null}
            </div>
          </div>

          <div className={styles.field}>
            <div className={styles.label}>Username / Display name</div>
            <input
              className={styles.input}
              value={profile.full_name}
              onChange={(e) => setProfile((prev) => ({ ...prev, full_name: e.target.value }))}
              placeholder="Your display name"
              aria-label="Display name"
              name="nickname"
              autoComplete="nickname"
            />
          </div>

          <div className={styles.field}>
            <div className={styles.label}>Profile picture link (optional)</div>
            <input
              className={styles.input}
              value={linkValue}
              onChange={(e) => setProfile((prev) => ({ ...prev, avatar_url: e.target.value.trim() }))}
              placeholder="https://example.com/photo.jpg"
              aria-label="Profile picture link (optional)"
              type="url"
              name="avatar-url"
              autoComplete="url"
              inputMode="url"
            />
          </div>

          <div className={styles.rowWrap} style={{ marginTop: 14 }}>
            <button type="button" className={styles.primary} onClick={saveProfile} disabled={loading}>
              Save profile
            </button>
            <button type="button" className={styles.secondary} onClick={refresh} disabled={loading}>
              Reload
            </button>
          </div>

          {message ? <p className={messageClass}>{message}</p> : null}
        </section>

        <BrokerProviderCard />

        <form
          className={styles.card}
          aria-label="Change password"
          onSubmit={(e) => {
            e.preventDefault()
            changePassword()
          }}
        >
          <p className={styles.cardTitle}>Change Password</p>
          {/* Password managers pair saved passwords with this field instead of guessing from the
              profile fields above (which made them autofill an email into the display name). */}
          <input type="email" name="username" autoComplete="username" value={profile.email || ''} readOnly hidden />

          <div className={styles.field}>
            <div className={styles.label}>Current Password</div>
            <input
              type="password"
              className={styles.input}
              value={passwordForm.currentPassword}
              onChange={(e) => setPasswordForm((prev) => ({ ...prev, currentPassword: e.target.value }))}
              name="current-password"
              autoComplete="current-password"
            />
          </div>

          <div className={styles.field}>
            <div className={styles.label}>New Password</div>
            <input
              type="password"
              className={styles.input}
              value={passwordForm.newPassword}
              onChange={(e) => setPasswordForm((prev) => ({ ...prev, newPassword: e.target.value }))}
              name="new-password"
              autoComplete="new-password"
            />
          </div>

          <div className={styles.field}>
            <div className={styles.label}>Confirm New Password</div>
            <input
              type="password"
              className={styles.input}
              value={passwordForm.confirmNewPassword}
              onChange={(e) => setPasswordForm((prev) => ({ ...prev, confirmNewPassword: e.target.value }))}
              name="confirm-new-password"
              autoComplete="new-password"
            />
          </div>

          <div className={styles.rowWrap} style={{ marginTop: 14 }}>
            <button type="submit" className={styles.primary} disabled={loading}>
              Change Password
            </button>
          </div>

          {passwordError ? <p className={`${styles.msg} ${styles.msgError}`}>{passwordError}</p> : null}
          {passwordMessage ? <p className={`${styles.msg} ${styles.msgSuccess}`}>{passwordMessage}</p> : null}
        </form>

        <DeleteAccountCard />
      </div>
    </AppShell>
  )
}