import { useEffect, useMemo, useState } from 'react'
import AppShell from '../components/AppShell'
import { apiFetch } from '../lib/api'
import { loadStoredProfile, saveStoredProfile } from '../lib/userProfile'
import styles from '../styles/Manage.module.css'

function initials(value) {
  const text = String(value || '').trim()
  if (!text) return 'U'
  const parts = text.split(/\s+/).filter(Boolean)
  const first = parts[0]?.[0] || ''
  const last = parts.length > 1 ? parts[parts.length - 1]?.[0] || '' : ''
  return `${first}${last}`.toUpperCase() || 'U'
}

export default function ProfilePage() {
  const [user, setUser] = useState(null)
  const [profile, setProfile] = useState({ full_name: '', avatar_url: '', email: '' })
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState(null)

  function handleAvatarUpload(event) {
    const file = event.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      setProfile((prev) => ({ ...prev, avatar_url: String(reader.result || '') }))
    }
    reader.readAsDataURL(file)
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
      setProfile({
        full_name: stored?.full_name || data?.profile?.full_name || '',
        avatar_url: stored?.avatar_url || data?.profile?.avatar_url || '',
        email: data?.profile?.email || data?.user?.email || '',
      })
    } catch (e) {
      setMessage(`Error: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  async function saveProfile() {
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
      setMessage('Profile saved')
    } catch (e) {
      setMessage(`Profile saved locally${e?.message ? ` (backend sync failed: ${e.message})` : ''}`)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  const avatar = profile.avatar_url?.trim()
  const label = profile.full_name?.trim() || profile.email || 'User'

  return (
    <AppShell title="Profile & Settings" subtitle="Update your username and profile picture">
      <div className={styles.page}>
        <section className={styles.card} aria-label="Profile editor">
          <div className={styles.rowWrap} style={{ alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <p className={styles.cardTitle}>User Profile</p>
              <div className={styles.muted} style={{ marginTop: 6 }}>
                {profile.email || 'Signed in user'}
              </div>
            </div>
            <div
              style={{
                width: 72,
                height: 72,
                borderRadius: '999px',
                overflow: 'hidden',
                border: '1px solid var(--aj-border)',
                background: 'var(--aj-surface)',
                display: 'grid',
                placeItems: 'center',
                boxShadow: 'var(--aj-shadow-sm)',
              }}
              aria-hidden="true"
            >
              {avatar ? (
                <img src={avatar} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
              ) : (
                <span style={{ color: 'var(--aj-indigo)', fontWeight: 900, fontSize: 20 }}>{initials(label)}</span>
              )}
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
            />
          </div>

          <div className={styles.field}>
            <div className={styles.label}>Profile picture</div>
            <input
              className={styles.input}
              type="file"
              accept="image/*"
              onChange={handleAvatarUpload}
              aria-label="Upload profile picture"
            />
            <input
              className={styles.input}
              value={profile.avatar_url}
              onChange={(e) => setProfile((prev) => ({ ...prev, avatar_url: e.target.value }))}
              placeholder="Paste image URL or data URL"
              aria-label="Profile picture URL"
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
      </div>
    </AppShell>
  )
}