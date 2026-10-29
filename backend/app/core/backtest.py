"""Backtest: replays one asset's trading bot over past daily prices, to see how its rules would
have done before they trade for real.

It reuses the live bot's own logic (strategy decision, exit rules, LightGBM forecaster) and keeps
the test honest:
- Walk-forward AI: on each day the model is trained only on the 180 days before it (as live does),
  never on later prices, so it cannot "see the future".
- A decision made at a day's close fills at the next day's open, with fees and slippage.
- Stop-loss / trailing stop / take-profit use each day's low and high. If a stop and the take-profit
  could both have been hit on the same day, the stop is assumed to have come first (pessimistic).

Simplifications (shown to the user): one bot decision per day (the live bot can act every few
minutes), news sentiment only for the days AJTrade has saved news (other days count as neutral), and
the daily loss limit is not modelled (with one trade per day it cannot trigger before that trade).
"""
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger('ajtrade.backtest')

TRAIN_BARS = 180          # same window the live model trains on
TREND_BARS = 21           # ~1 month: the live "1M price change" the strategies use
PERIOD_DAYS = {'3m': 91, '6m': 182, '1y': 365, '5y': 1826, '10y': 3652, '20y': 7305}
# Yahoo range fetched per period: up to 1 year needs ~2 years (180 training days first); longer
# periods fetch the full daily history and trim it (see load_daily_history).
_HISTORY_RANGE = {'3m': 'two_years', '6m': 'two_years', '1y': 'two_years', '5y': 'ten_years'}
_WARMUP_DAYS = 400       # calendar days kept before the test: covers 180 trading days of training
MIN_TEST_BARS = 20
DEFAULT_RETRAIN_EVERY = 5  # retrain weekly; live retrains every refresh, but daily refits barely differ
DEFAULT_TRADE_AMOUNT = 1000.0
CRYPTO_FEE_PCT = 0.25      # Alpaca crypto taker fee
STOCK_FEE_PCT = 0.0        # Alpaca US stocks: commission-free
SLIPPAGE_PCT = 0.05


def is_crypto(symbol: str) -> bool:
    return str(symbol or '').upper().endswith('-USD')


# --- Signals --------------------------------------------------------------------------------------

def walk_forward_signals(
    df: pd.DataFrame,
    sentiment: pd.Series,
    *,
    first_decision: int,
    retrain_every: int = DEFAULT_RETRAIN_EVERY,
    make_forecaster: Optional[Callable[[], Any]] = None,
) -> Dict[int, Dict[str, Any]]:
    """AI outlook at the close of every bar from first_decision on, using only bars up to that one.

    Returns {bar index: {signal, probability_up, confidence}}. A bar where the model can't be
    trained gets signal HOLD with model_fallback = True (the bot does not trade on it, as live).
    """
    if make_forecaster is None:
        from app.ml.market_forecaster import MarketForecaster

        def make_forecaster():
            return MarketForecaster(n_jobs=1)

    template = make_forecaster()
    # Features of each bar only use that bar and the ones before it (returns, 5/10-day averages).
    features = template._build_features(df, sentiment)
    out: Dict[int, Dict[str, Any]] = {}
    model = None
    for i in range(first_decision, len(df)):
        if model is None or (i - first_decision) % max(int(retrain_every), 1) == 0:
            start = max(0, i - TRAIN_BARS + 1)
            candidate = make_forecaster()
            try:
                candidate.train_model(df.iloc[start:i + 1], sentiment.iloc[start:i + 1])
                model = candidate
            except Exception as exc:
                logger.debug('backtest_train_failed bar=%s error=%s', i, exc)
                model = None
        if model is None:
            out[i] = {'signal': 'HOLD', 'probability_up': None, 'confidence': 0, 'model_fallback': True}
            continue
        row = features.iloc[[i]][model.feature_columns]
        prob_up = float(model.model.predict_proba(row)[0, 1])
        if prob_up >= model.buy_threshold:
            signal = 'BUY'
        elif prob_up <= model.sell_threshold:
            signal = 'SELL'
        else:
            signal = 'HOLD'
        out[i] = {
            'signal': signal,
            'probability_up': prob_up,
            'confidence': int(round(max(prob_up, 1 - prob_up) * 100)),
            'model_fallback': False,
        }
    return out


# --- Simulation -----------------------------------------------------------------------------------

@dataclass
class _Position:
    qty: float = 0.0
    avg_price: float = 0.0      # average fill price (what stops and targets are measured from, as live)
    cost: float = 0.0           # cash spent on the units still held, fees included (for P/L)
    trailing_level: float = 0.0
    trades: List[Dict[str, Any]] = field(default_factory=list)


def _pct(rule: Dict[str, Any], key: str) -> float:
    try:
        return max(float(rule.get(key) or 0.0), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _date(ts: Any) -> str:
    return pd.Timestamp(ts).date().isoformat()


def simulate(
    df: pd.DataFrame,
    signals: Dict[int, Dict[str, Any]],
    sentiment: pd.Series,
    rule: Dict[str, Any],
    *,
    start_index: int,
    start_cash: float,
    fee_pct: float,
    slippage_pct: float = SLIPPAGE_PCT,
    min_confidence: int = 70,
    symbol: str = '',
    end_index: Optional[int] = None,
) -> Dict[str, Any]:
    """Runs the bot from bar start_index up to (not including) end_index, by default the last bar.
    signals[i] is the outlook at the close of bar i, acted on at the open of bar i + 1."""
    end_index = len(df) if end_index is None else min(int(end_index), len(df))
    from app.api.trading_bot import DCA_STRATEGY, decide_strategy_side
    from app.core import trade_stats

    strategy = str(rule.get('strategy') or 'Trend Following')
    stop_pct = _pct(rule, 'stop_loss_pct')
    trail_pct = _pct(rule, 'trailing_stop_pct')
    tp_pct = _pct(rule, 'take_profit_pct')
    amount = float(rule.get('max_capital') or 0) or DEFAULT_TRADE_AMOUNT
    dca_hours = int(rule.get('dca_interval_hours') or 24)
    fee = fee_pct / 100.0
    slip = slippage_pct / 100.0

    cash = float(start_cash)
    pos = _Position()
    trades: List[Dict[str, Any]] = []
    skipped_cash = 0
    in_market_days = 0
    equity: List[Dict[str, Any]] = []
    last_dca_at: Optional[pd.Timestamp] = None

    first_open = float(df['Open'].iloc[start_index])
    hold_qty = start_cash * (1 - fee) / (first_open * (1 + slip)) if first_open > 0 else 0.0

    def buy(i: int, price: float, reason: str) -> None:
        nonlocal cash, skipped_cash
        if cash + 1e-9 < amount:
            skipped_cash += 1
            return
        fill = price * (1 + slip)
        qty = amount * (1 - fee) / fill
        new_qty = pos.qty + qty
        pos.avg_price = (pos.avg_price * pos.qty + fill * qty) / new_qty
        pos.qty = new_qty
        pos.cost += amount
        cash -= amount
        if trail_pct > 0:
            pos.trailing_level = max(pos.trailing_level, fill * (1 - trail_pct / 100))
        trades.append({'date': _date(df.index[i]), 'side': 'BUY', 'price': round(fill, 6), 'qty': qty,
                       'amount': round(amount, 2), 'reason': reason, 'pl': None})

    def sell(i: int, price: float, qty: float, reason: str, *, apply_slippage: bool = True) -> None:
        nonlocal cash
        qty = min(qty, pos.qty)
        if qty <= 0:
            return
        fill = price * (1 - slip) if apply_slippage else price
        proceeds = qty * fill * (1 - fee)
        basis = pos.cost * (qty / pos.qty)
        pl = proceeds - basis
        cash += proceeds
        pos.cost -= basis
        pos.qty -= qty
        if pos.qty <= 1e-12:
            pos.qty, pos.cost, pos.avg_price, pos.trailing_level = 0.0, 0.0, 0.0, 0.0
        trades.append({'date': _date(df.index[i]), 'side': 'SELL', 'price': round(fill, 6), 'qty': qty,
                       'amount': round(qty * fill, 2), 'reason': reason, 'pl': round(pl, 2)})

    for i in range(start_index, end_index):
        bar = df.iloc[i]
        o, h, l, c = float(bar['Open']), float(bar['High']), float(bar['Low']), float(bar['Close'])
        ts = pd.Timestamp(df.index[i])

        # 1) Act at the open on yesterday's close decision.
        decision = signals.get(i - 1)
        if strategy == DCA_STRATEGY:
            due = last_dca_at is None or (ts - last_dca_at) >= pd.Timedelta(hours=dca_hours) - pd.Timedelta(minutes=1)
            if due:
                last_dca_at = ts
                buy(i, o, f'Scheduled buy every {dca_hours}h')
        elif decision is not None and i - 1 >= TREND_BARS:
            past = float(df['Close'].iloc[i - 1 - TREND_BARS])
            insight = {
                **decision,
                'price_change_pct': (float(df['Close'].iloc[i - 1]) / past - 1) * 100 if past > 0 else 0.0,
                'latest_sentiment_score': float(sentiment.iloc[i - 1]) if len(sentiment) > i - 1 else 0.0,
            }
            side, reason = decide_strategy_side(strategy, insight, min_confidence)
            if side == 'BUY':
                buy(i, o, reason)
            elif side == 'SELL' and pos.qty > 0:
                sell(i, o, amount / (o * (1 - slip)), reason)

        # 2) Exits during the day, as the live bot checks them every minute.
        if pos.qty > 0:
            stops = []
            if stop_pct > 0:
                stops.append((pos.avg_price * (1 - stop_pct / 100), 'Stop-Loss'))
            if trail_pct > 0 and pos.trailing_level > pos.avg_price:
                stops.append((pos.trailing_level, 'Trailing Stop'))
            stop_price, stop_name = max(stops) if stops else (None, None)
            target = pos.avg_price * (1 + tp_pct / 100) if tp_pct > 0 else None
            if stop_price is not None and l <= stop_price:
                # A gap below the stop fills at the open, not at the stop.
                sell(i, min(o, stop_price), pos.qty, stop_name, apply_slippage=o > stop_price)
            elif target is not None and h >= target:
                sell(i, max(o, target), pos.qty, 'Take-Profit', apply_slippage=False)
            elif trail_pct > 0:
                pos.trailing_level = max(pos.trailing_level, h * (1 - trail_pct / 100))

        in_market_days += int(pos.qty > 0)
        equity.append({'date': _date(ts), 'bot': round(cash + pos.qty * c, 2), 'hold': round(hold_qty * c, 2)})

    closed = [{'symbol': symbol, 'pl': t['pl'], 'at': None} for t in trades if t['side'] == 'SELL']
    stats = trade_stats.summarize(closed)
    last_close = float(df['Close'].iloc[end_index - 1])
    open_pl = pos.qty * last_close * (1 - fee) - pos.cost if pos.qty > 0 else None
    # A position still open at the end counts as one more trade, marked at the last price: otherwise
    # losers that are never sold would make the win rate look better than it is.
    with_open = closed + ([{'symbol': symbol, 'pl': open_pl, 'at': None}] if open_pl is not None else [])
    stats_open = trade_stats.summarize(with_open)
    final = equity[-1]['bot'] if equity else start_cash
    hold_final = equity[-1]['hold'] if equity else start_cash
    return {
        'equity': equity,
        'trades': trades,
        'final_equity': final,
        'open_position': {
            'qty': pos.qty,
            'avg_price': round(pos.avg_price, 6),
            'value': round(pos.qty * last_close, 2),
            'unrealized_pl': round(open_pl, 2),
        } if pos.qty > 0 else None,
        'metrics': {
            'start_cash': round(start_cash, 2),
            'final_equity': round(final, 2),
            'return_pct': round((final / start_cash - 1) * 100, 2) if start_cash else 0.0,
            'hold_return_pct': round((hold_final / start_cash - 1) * 100, 2) if start_cash else 0.0,
            'max_drawdown_pct': max_drawdown_pct([e['bot'] for e in equity]),
            'hold_max_drawdown_pct': max_drawdown_pct([e['hold'] for e in equity]),
            'buys': sum(1 for t in trades if t['side'] == 'BUY'),
            'sells': sum(1 for t in trades if t['side'] == 'SELL'),
            'skipped_no_cash': skipped_cash,
            'exposure_pct': round(in_market_days / len(equity) * 100, 1) if equity else 0.0,
            **{k: stats[k] for k in ('closed_trades', 'winning_trades', 'losing_trades', 'win_rate', 'realized_pl')},
            'win_rate_incl_open': stats_open['win_rate'],
            'trades_incl_open': stats_open['closed_trades'],
        },
    }


def max_drawdown_pct(values: List[float]) -> float:
    """Largest fall from a peak, in % (0 when the curve never falls)."""
    peak = -math.inf
    worst = 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            worst = min(worst, (v / peak - 1) * 100)
    return round(worst, 2)


def model_accuracy(df: pd.DataFrame, signals: Dict[int, Dict[str, Any]], start: int) -> Dict[str, Any]:
    """How often the model's direction matched the next day's close, over the test period."""
    hits = total = calls_hit = calls = 0
    for i, s in signals.items():
        if i < start - 1 or i + 1 >= len(df) or s.get('probability_up') is None:
            continue
        went_up = float(df['Close'].iloc[i + 1]) > float(df['Close'].iloc[i])
        total += 1
        hits += int((s['probability_up'] >= 0.5) == went_up)
        if s['signal'] in ('BUY', 'SELL'):
            calls += 1
            calls_hit += int((s['signal'] == 'BUY') == went_up)
    return {
        'direction_accuracy_pct': round(hits / total * 100, 1) if total else None,
        'days': total,
        'outlook_accuracy_pct': round(calls_hit / calls * 100, 1) if calls else None,
        'outlook_days': calls,
    }


# --- Data -----------------------------------------------------------------------------------------

def load_daily_history(symbol: str, period: str = '1y') -> pd.DataFrame:
    """Daily OHLCV covering the period plus the 180-day training window before it (prices are
    split-adjusted by Yahoo; dividends are not included). Assets younger than the period return what
    exists."""
    from app.api import market
    from app.api.ml_v2 import _history_payload_to_ohlcv_df

    payload = market._fetch_history(market._normalize_symbol(symbol), _HISTORY_RANGE.get(period, 'max_daily'))
    df = _history_payload_to_ohlcv_df(payload)
    if df.empty:
        return df
    # One row per day (Yahoo can repeat today's partial bar).
    df = df[~df.index.normalize().duplicated(keep='last')]
    # Keep only what the test needs, so a 10-year test of a 40-year-old stock stays a 10-year test.
    keep_from = pd.Timestamp(df.index[-1]) - pd.Timedelta(days=PERIOD_DAYS.get(period, 365) + _WARMUP_DAYS)
    return df[df.index >= keep_from]


def sentiment_series(df: pd.DataFrame, daily: List[Dict[str, Any]]) -> pd.Series:
    """Daily news sentiment for each bar from the saved news history, aligned like the live model
    (carried forward with the same daily fade); days before any saved news are neutral (0)."""
    from app.api.ml_v2 import _build_sentiment_series

    items = []
    for d in daily or []:
        try:
            items.append({'published_date': pd.Timestamp(d['date']), 'score': float(d['avg'])})
        except (KeyError, TypeError, ValueError):
            continue
    return _build_sentiment_series(df, items)


# --- Orchestration --------------------------------------------------------------------------------

# The AI outlooks don't depend on the bot's settings, so re-running with other stops or amounts is instant.
_SIGNAL_CACHE: Dict[Tuple[str, str, int], Tuple[float, pd.DataFrame, pd.Series, Dict[int, Dict[str, Any]]]] = {}
_SIGNAL_TTL_SECONDS = 3600


def start_index_for(df: pd.DataFrame, period: str) -> Tuple[int, List[str]]:
    """First test bar for the period, after the training window; warns when history is short."""
    warnings: List[str] = []
    earliest = TRAIN_BARS
    last = pd.Timestamp(df.index[-1])
    cutoff = last - pd.Timedelta(days=PERIOD_DAYS.get(period, 365))
    idx = int(pd.DatetimeIndex(df.index).searchsorted(cutoff))
    if idx < earliest:
        warnings.append(
            f'Price history only goes back to {_date(df.index[0])}, so the test starts on '
            f'{_date(df.index[min(earliest, len(df) - 1)])}, after the 180 days the AI needs to learn from.'
        )
        idx = earliest
    if len(df) - idx < MIN_TEST_BARS:
        raise ValueError(f'Not enough price history to backtest (needs at least {TRAIN_BARS + MIN_TEST_BARS} days).')
    return idx, warnings


def _prepared(symbol: str, period: str, news_daily, retrain_every: int):
    """Price history, sentiment and walk-forward AI outlooks for the period (cached for an hour)."""
    key = (symbol, period, int(retrain_every))
    cached = _SIGNAL_CACHE.get(key)
    if cached and time.time() - cached[0] < _SIGNAL_TTL_SECONDS:
        _, df, sentiment, signals = cached
        start, warnings = start_index_for(df, period)
        return df, sentiment, signals, start, warnings
    df = load_daily_history(symbol, period)
    if df.empty:
        raise ValueError(f'No daily price history for {symbol}.')
    start, warnings = start_index_for(df, period)
    sentiment = sentiment_series(df, news_daily or [])
    signals = walk_forward_signals(df, sentiment, first_decision=start - 1, retrain_every=retrain_every)
    _SIGNAL_CACHE[key] = (time.time(), df, sentiment, signals)
    return df, sentiment, signals, start, warnings


def run_backtest(
    symbol: str,
    rule: Dict[str, Any],
    *,
    period: str = '1y',
    start_cash: float = 10000.0,
    news_daily: Optional[List[Dict[str, Any]]] = None,
    min_confidence: int = 70,
    retrain_every: int = DEFAULT_RETRAIN_EVERY,
) -> Dict[str, Any]:
    """Blocking (CPU + network): call it from a worker thread."""
    symbol = str(symbol or '').upper()
    started = time.monotonic()
    df, sentiment, signals, start, warnings = _prepared(symbol, period, news_daily, retrain_every)

    fee_pct = CRYPTO_FEE_PCT if is_crypto(symbol) else STOCK_FEE_PCT
    result = simulate(
        df, signals, sentiment, rule,
        start_index=start, start_cash=start_cash, fee_pct=fee_pct,
        min_confidence=min_confidence, symbol=symbol,
    )
    news_days = sum(1 for v in sentiment.iloc[start:] if abs(float(v)) > 1e-9)
    test_bars = len(df) - start
    counts = {'BUY': 0, 'SELL': 0, 'HOLD': 0}
    for i in range(start - 1, len(df) - 1):
        s = signals.get(i)
        if s:
            counts[s['signal']] = counts.get(s['signal'], 0) + 1
    if news_days < test_bars * 0.5 and str(rule.get('strategy')) in ('News Momentum', 'AI Momentum + Sentiment'):
        warnings.append(
            f'News sentiment is only known for {news_days} of the {test_bars} test days (AJTrade saves the last '
            '30 days of news), so this sentiment-based strategy is mostly tested without news.'
        )
    return {
        'symbol': symbol,
        'period': period,
        'start': _date(df.index[start]),
        'end': _date(df.index[-1]),
        'test_days': test_bars,
        'strategy': str(rule.get('strategy') or 'Trend Following'),
        **result,
        'model': {**model_accuracy(df, signals, start), 'outlooks': counts, 'retrain_every_days': retrain_every},
        'assumptions': {
            'fee_pct': fee_pct,
            'slippage_pct': SLIPPAGE_PCT,
            'trade_amount': float(rule.get('max_capital') or 0) or DEFAULT_TRADE_AMOUNT,
            'min_confidence': min_confidence,
            'news_days': news_days,
        },
        'warnings': warnings,
        'elapsed_s': round(time.monotonic() - started, 1),
    }


# --- Auto-tuning ----------------------------------------------------------------------------------

TUNE_STRATEGIES = ('Trend Following', 'Mean Reversion', 'AI Momentum + Sentiment')
# A stop-loss is always on: without one, a high win rate just means losers are held instead of sold.
TUNE_STOP_LOSS = (3, 5, 8, 12, 15)
TUNE_TRAILING = (0, 3, 5, 8)
TUNE_TAKE_PROFIT = (0, 2, 3, 5, 8, 12, 20)
TUNE_SHARE = 2 / 3        # tune on the first 2/3 of the year, check on the last 1/3 it never saw
MIN_TUNE_TRADES = 3       # fewer trades say nothing (1 of 1 is "100%")
_TUNE_CACHE: Dict[Tuple[str, int, float, int], Tuple[float, Dict[str, Any]]] = {}


def _summary(result: Dict[str, Any], df: pd.DataFrame, start: int, end: int) -> Dict[str, Any]:
    m = result['metrics']
    return {
        'start': _date(df.index[start]),
        'end': _date(df.index[end - 1]),
        'days': end - start,
        'win_rate': m['win_rate_incl_open'],
        'trades': m['trades_incl_open'],
        'return_pct': m['return_pct'],
        'hold_return_pct': m['hold_return_pct'],
        'max_drawdown_pct': m['max_drawdown_pct'],
    }


def _score(m: Dict[str, Any]) -> Tuple[float, float]:
    return (float(m['return_pct']), float(m['max_drawdown_pct']))


def tune_rule(
    symbol: str,
    *,
    target_win_rate: float = 60.0,
    min_trades: int = MIN_TUNE_TRADES,
    news_daily: Optional[List[Dict[str, Any]]] = None,
    min_confidence: int = 70,
    trade_amount: float = DEFAULT_TRADE_AMOUNT,
    start_cash: float = 10000.0,
) -> Dict[str, Any]:
    """Searches strategy and exit settings for ones that reached the target win rate while making
    money, using the first 2/3 of the last year, then reports how they did on the last 1/3.

    Win rate counts a position still open at the end as a trade (marked at the last price). Blocking:
    call it from a worker thread.
    """
    symbol = str(symbol or '').upper()
    key = (symbol, int(min_confidence), float(target_win_rate), int(min_trades))
    cached = _TUNE_CACHE.get(key)
    if cached and time.time() - cached[0] < _SIGNAL_TTL_SECONDS:
        return cached[1]
    started = time.monotonic()
    df, sentiment, signals, start, warnings = _prepared(symbol, '1y', news_daily, DEFAULT_RETRAIN_EVERY)
    end = len(df)
    split = start + int((end - start) * TUNE_SHARE)
    fee_pct = CRYPTO_FEE_PCT if is_crypto(symbol) else STOCK_FEE_PCT

    def run(rule: Dict[str, Any], a: int, b: int) -> Dict[str, Any]:
        return simulate(df, signals, sentiment, rule, start_index=a, end_index=b, start_cash=start_cash,
                        fee_pct=fee_pct, min_confidence=min_confidence, symbol=symbol)

    tested = 0
    qualifying: List[Tuple[Tuple[float, float], Dict[str, Any], Dict[str, Any]]] = []
    fallback: List[Tuple[Tuple[float, float, float], Dict[str, Any], Dict[str, Any]]] = []
    for strategy in TUNE_STRATEGIES:
        for sl in TUNE_STOP_LOSS:
            for tr in TUNE_TRAILING:
                for tp in TUNE_TAKE_PROFIT:
                    rule = {'strategy': strategy, 'stop_loss_pct': sl, 'trailing_stop_pct': tr,
                            'take_profit_pct': tp, 'max_capital': trade_amount}
                    m = run(rule, start, split)['metrics']
                    tested += 1
                    if m['trades_incl_open'] < min_trades:
                        continue
                    win = float(m['win_rate_incl_open'] or 0)
                    if win >= target_win_rate and m['return_pct'] > 0:
                        qualifying.append((_score(m), rule, m))
                    else:
                        fallback.append(((1.0 if m['return_pct'] > 0 else 0.0, win, float(m['return_pct'])), rule, m))

    reached = bool(qualifying)
    if qualifying:
        _, rule, _ = max(qualifying, key=lambda item: item[0])
    elif fallback:
        # Closest: profitable first, then the highest win rate, then the best return.
        _, rule, _ = max(fallback, key=lambda item: item[0])
    else:
        rule = {'strategy': 'Trend Following', 'stop_loss_pct': 5, 'trailing_stop_pct': 0,
                'take_profit_pct': 0, 'max_capital': trade_amount}

    tuning = _summary(run(rule, start, split), df, start, split)
    check = _summary(run(rule, split, end), df, split, end)
    full = _summary(run(rule, start, end), df, start, end)
    if check['trades'] and check['win_rate'] is not None and check['win_rate'] < target_win_rate:
        warnings.append(
            f"On the last {check['days']} days, which the search never saw, {check['win_rate']:.0f}% of these "
            f"settings' simulated trades closed in profit: below {target_win_rate:.0f}%."
        )
    result = {
        'symbol': symbol,
        'target_win_rate': target_win_rate,
        'reached': reached,
        'rule': rule,
        'tuning': tuning,
        'check': check,
        'full': full,
        'tested': tested,
        'qualifying': len(qualifying),
        'model': model_accuracy(df, signals, start),
        'warnings': warnings,
        'elapsed_s': round(time.monotonic() - started, 1),
    }
    _TUNE_CACHE[key] = (time.time(), result)
    return result
