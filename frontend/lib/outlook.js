// The model's direction call is shown as a market outlook, never as an instruction to trade.
// Telling the public to buy or sell a security is investment advice, which needs an advisor
// licence (e.g. Thai SEC Act s.4/s.90), so BUY/SELL/HOLD stay internal codes only.
export const OUTLOOK_LABELS = { BUY: 'Bullish', SELL: 'Bearish', HOLD: 'Neutral' }

export function outlookLabel(signal) {
  const code = String(signal || '').toUpperCase()
  return OUTLOOK_LABELS[code] || (code ? code.charAt(0) + code.slice(1).toLowerCase() : 'Neutral')
}

export const NOT_ADVICE =
  'AI outlooks are statistical forecasts for research and education. They are not investment advice or a recommendation to buy or sell any asset.'
