// Turns an AI insight into a suggested bot setup for that asset. Used by the Insights page (preview +
// "Automate") and the Automated page (applies it as unsaved changes the user reviews, saves or undoes).

export const AUTO_CONFIG_KEY = 'ajtrade.autoConfig'
const MEAN_REVERSION_MOVE_PCT = 3
const NEWS_MOMENTUM_SENTIMENT = 0.3

function round1(value) {
  return Math.round(value * 10) / 10
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max)
}

function pickStrategy(insight) {
  const signal = String(insight?.signal || 'HOLD').toUpperCase()
  const change = Number(insight?.price_change_pct) || 0
  const sentiment = Number(insight?.latest_sentiment_score) || 0
  const changeText = `${change > 0 ? '+' : ''}${change.toFixed(1)}%`

  if (insight?.model_fallback || insight?.confidence == null) {
    return {
      strategy: 'News Momentum',
      reason: 'The AI model is unavailable for this asset, so the bot trades on strong news sentiment only.',
    }
  }
  if (signal === 'BUY' || signal === 'SELL') {
    const trendAgrees = (signal === 'BUY' && change > 0) || (signal === 'SELL' && change < 0)
    const counterMove = (signal === 'BUY' && change <= -MEAN_REVERSION_MOVE_PCT) || (signal === 'SELL' && change >= MEAN_REVERSION_MOVE_PCT)
    const sentimentAgrees = (signal === 'BUY' && sentiment > 0) || (signal === 'SELL' && sentiment < 0)
    if (trendAgrees) {
      return { strategy: 'Trend Following', reason: `The AI ${signal} agrees with the ${changeText} one-month trend.` }
    }
    if (counterMove) {
      return {
        strategy: 'Mean Reversion',
        reason: `The AI says ${signal} after a ${changeText} move, i.e. it expects the price to swing back.`,
      }
    }
    if (sentimentAgrees) {
      return { strategy: 'AI Momentum + Sentiment', reason: `The AI ${signal} is backed by news sentiment (${sentiment.toFixed(2)}).` }
    }
    return { strategy: 'Trend Following', reason: `The AI says ${signal}, but the trend is flat; the bot waits for the trend to confirm it.` }
  }
  if (Math.abs(sentiment) >= NEWS_MOMENTUM_SENTIMENT) {
    return { strategy: 'News Momentum', reason: `No AI trade signal right now, but news sentiment is strong (${sentiment.toFixed(2)}).` }
  }
  return { strategy: 'Trend Following', reason: 'No AI trade signal right now; the bot waits for a signal that the trend confirms.' }
}

// Exits scaled to how much the asset normally moves in a day (volatility = std. dev. of daily returns).
function pickRisk(insight) {
  const vol = Number(insight?.volatility_pct)
  if (!Number.isFinite(vol) || vol <= 0) {
    return { stopLossPct: 2, trailingStopLossPct: 1.2, takeProfitPct: 5, reason: 'Default exits (volatility not available yet).' }
  }
  const stopLossPct = round1(clamp(vol * 2, 1, 15))
  return {
    stopLossPct,
    trailingStopLossPct: round1(clamp(vol * 1.5, 0.5, 10)),
    takeProfitPct: round1(clamp(stopLossPct * 2, 1.5, 40)),
    reason: `Exits sized to a typical daily move of ${vol.toFixed(1)}%: stop-loss 2×, trailing stop 1.5×, take-profit 2× the stop-loss.`,
  }
}

export function suggestBotConfig(insight) {
  const { strategy, reason: strategyReason } = pickStrategy(insight)
  const { reason: riskReason, ...risk } = pickRisk(insight)
  return {
    asset: String(insight?.symbol || '').toUpperCase(),
    rule: { isActive: true, strategy, ...risk },
    reasons: [strategyReason, riskReason],
  }
}

/** Hands a suggested setup to the Automated page (read once, within a few minutes). */
export function storeAutoConfig(config) {
  try {
    window.sessionStorage.setItem(AUTO_CONFIG_KEY, JSON.stringify({ ...config, createdAt: Date.now() }))
  } catch {
    // Storage unavailable: the Automated page simply opens without a pre-filled setup.
  }
}

export function takeAutoConfig() {
  try {
    const raw = window.sessionStorage.getItem(AUTO_CONFIG_KEY)
    window.sessionStorage.removeItem(AUTO_CONFIG_KEY)
    if (!raw) return null
    const config = JSON.parse(raw)
    if (!config?.asset || !config?.rule || Date.now() - Number(config.createdAt || 0) > 10 * 60 * 1000) return null
    return config
  } catch {
    return null
  }
}
