// Shared number formatting that also works for crypto (e.g. 0.00012 BTC, DOGE at $0.0943).

/** Crypto pairs use Yahoo's "<COIN>-USD" form across the app (the backend maps them to Alpaca's "BTC/USD"). */
export function isCryptoSymbol(symbol) {
  return /^[A-Z0-9]+-USD$/.test(String(symbol || '').trim().toUpperCase())
}

/** "coins" for crypto pairs, "shares" otherwise. */
export function unitWord(symbol, { capitalize = false } = {}) {
  const word = isCryptoSymbol(symbol) ? 'coins' : 'shares'
  return capitalize ? word[0].toUpperCase() + word.slice(1) : word
}

/** Keeps 4 significant digits below 1 (0.00012345 -> 0.0001235) instead of rounding to 0.0001. */
export function formatQuantity(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '0'
  if (n !== 0 && Math.abs(n) < 1) return n.toLocaleString(undefined, { maximumSignificantDigits: 4 })
  return n.toLocaleString(undefined, { maximumFractionDigits: 4 })
}

/** Currency options: 2 decimals normally, up to 4 below $1 so sub-dollar coins keep their price. */
export function usdOptions(value) {
  const n = Math.abs(Number(value))
  return {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: n > 0 && n < 1 ? 4 : 2,
  }
}
