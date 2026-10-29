// Turns an AI insight into a suggested bot setup for that asset. Used by the Insights page (preview +
// "Automate") and the Automated page (applies it as unsaved changes the user reviews, saves or undoes).

import { outlookLabel } from './outlook'

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
      return { strategy: 'Trend Following', reason: `The ${outlookLabel(signal)} AI outlook agrees with the ${changeText} one-month trend.` }
    }
    if (counterMove) {
      return {
        strategy: 'Mean Reversion',
        reason: `The AI outlook is ${outlookLabel(signal)} after a ${changeText} move, i.e. it expects the price to swing back.`,
      }
    }
    if (sentimentAgrees) {
      return { strategy: 'AI Momentum + Sentiment', reason: `The ${outlookLabel(signal)} AI outlook is backed by news sentiment (${sentiment.toFixed(2)}).` }
    }
    return { strategy: 'Trend Following', reason: `The AI outlook is ${outlookLabel(signal)}, but the trend is flat; the bot waits for the trend to confirm it.` }
  }
  if (Math.abs(sentiment) >= NEWS_MOMENTUM_SENTIMENT) {
    return { strategy: 'News Momentum', reason: `The AI outlook is Neutral right now, but news sentiment is strong (${sentiment.toFixed(2)}).` }
  }
  return { strategy: 'Trend Following', reason: 'The AI outlook is Neutral right now; the bot waits for an outlook that the trend confirms.' }
}

// Exits scaled to how much the asset normally moves in a day (volatility = std. dev. of daily returns).
function pickRisk(insight) {
  const vol = Number(insight?.volatility_pct)
  if (!Number.isFinite(vol) || vol <= 0) {
    return { stopLossPct: 5, trailingStopLossPct: 0, takeProfitPct: 0, reason: 'Default exit: a 5% stop-loss (volatility not available yet).' }
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

function signedPct(value) {
  const n = Number(value)
  return Number.isFinite(n) ? `${n > 0 ? '+' : ''}${n.toFixed(1)}%` : '--'
}

/**
 * Setup from the backend auto-tuner (GET /api/bot/autoconfig): the strategy and exits with the best
 * simulated return among those passing the backtest check. Keeps the result so the page can show it.
 * Wording stays neutral: a backtest describes the past, it is not a promise about future trades.
 */
export function tunedBotConfig(insight, tuned) {
  const rule = tuned?.rule || {}
  const full = tuned?.full || {}
  const check = tuned?.check || {}
  const target = Number(tuned?.target_win_rate || 60)
  const found = tuned?.reached
    ? `These settings passed the backtest check on the past year (simulated return ${signedPct(full.return_pct)}, buy & hold ${signedPct(full.hold_return_pct)}).`
    : `No settings passed the backtest check on the past year; these came closest (simulated return ${signedPct(full.return_pct)}).`
  const checkPassed = check.trades && Number(check.win_rate) >= target
  const checked = check.trades
    ? `On the last ${check.days} days, which the search did not see, they ${checkPassed ? 'also passed' : 'did not pass'} the same check.`
    : `They made no trades in the last ${check.days} days the search did not see.`
  return {
    asset: String(insight?.symbol || tuned?.symbol || '').toUpperCase(),
    rule: {
      isActive: true,
      strategy: rule.strategy || 'Trend Following',
      stopLossPct: Number(rule.stop_loss_pct ?? 5),
      trailingStopLossPct: Number(rule.trailing_stop_pct ?? 0),
      takeProfitPct: Number(rule.take_profit_pct ?? 0),
    },
    reasons: [found, checked, 'A backtest replays past prices; it does not predict or guarantee future results.'],
    tuned: { reached: Boolean(tuned?.reached), target, full, check },
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
