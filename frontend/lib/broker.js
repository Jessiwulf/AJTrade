import { useEffect, useMemo, useState } from 'react'
import useSWR from 'swr'
import { apiFetch } from './api'
import { setMoneyCurrency } from './format'

// The member's active broker (Profile & Settings): pages show only that broker's account and assets,
// so Alpaca (US stocks & crypto, USD) and Settrade (Thai SET stocks, THB) are never mixed.

export const BROKER_KEY = '/api/profile/broker'
const LAST_PROVIDER_KEY = 'ajtrade_broker_provider'

export const PROVIDER_META = {
  alpaca: { key: 'alpaca', label: 'Alpaca', market: 'US stocks & crypto', currency: 'USD' },
  settrade: { key: 'settrade', label: 'Settrade', market: 'Thai SET stocks', currency: 'THB' },
}

/** Provider for a symbol: the API's answer when known, else ".BK" means a SET stock. */
export function symbolProvider(symbol, knownProvider) {
  if (knownProvider === 'alpaca' || knownProvider === 'settrade') return knownProvider
  return String(symbol || '').trim().toUpperCase().endsWith('.BK') ? 'settrade' : 'alpaca'
}

export function useBroker() {
  const { data, error, mutate } = useSWR(BROKER_KEY, (url) => apiFetch(url), {
    revalidateOnFocus: true,
    revalidateIfStale: true,
    dedupingInterval: 15 * 1000,
  })
  // Last known provider avoids showing "$" for a moment before a Settrade member's setting loads.
  const [remembered, setRemembered] = useState(null)
  useEffect(() => {
    try {
      setRemembered(localStorage.getItem(LAST_PROVIDER_KEY))
    } catch {
      // storage unavailable
    }
  }, [])

  const provider = PROVIDER_META[data?.provider] ? data.provider : PROVIDER_META[remembered] ? remembered : 'alpaca'
  const meta = PROVIDER_META[provider]
  setMoneyCurrency(meta.currency)

  useEffect(() => {
    if (!data?.provider) return
    try {
      localStorage.setItem(LAST_PROVIDER_KEY, data.provider)
    } catch {
      // storage unavailable
    }
  }, [data?.provider])

  async function setProvider(next) {
    const updated = await apiFetch(BROKER_KEY, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider: next }),
    })
    try {
      localStorage.setItem(LAST_PROVIDER_KEY, updated.provider)
    } catch {
      // storage unavailable
    }
    await mutate(updated, false)
    return updated
  }

  return {
    provider,
    meta,
    providers: Array.isArray(data?.providers) ? data.providers : [],
    status: data?.providers?.find((p) => p.key === provider) || null,
    loaded: Boolean(data || error),
    setProvider,
  }
}

/**
 * Watchlist symbols with the broker that lists each one. `belongs(symbol)` tells whether an asset is
 * shown under the active provider (legacy bare SET tickers like "KCE" are resolved by the API).
 */
export function useProviderSymbols(provider) {
  const { data } = useSWR('/api/watchlist', (url) => apiFetch(url), { revalidateIfStale: true })
  return useMemo(() => {
    const items = Array.isArray(data) ? data : []
    const known = new Map(items.map((item) => [String(item.symbol || '').toUpperCase(), item.provider]))
    const belongs = (symbol) => symbolProvider(symbol, known.get(String(symbol || '').toUpperCase())) === provider
    return {
      items,
      visible: items.filter((item) => belongs(item.symbol)),
      hiddenCount: items.filter((item) => !belongs(item.symbol)).length,
      belongs,
    }
  }, [data, provider])
}
