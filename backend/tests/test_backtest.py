import numpy as np
import pandas as pd
import pytest

from app.core import backtest

RULE = {'strategy': 'Trend Following', 'stop_loss_pct': 0, 'trailing_stop_pct': 0, 'take_profit_pct': 0,
        'max_capital': 1000, 'dca_interval_hours': 24}


def bars(closes, *, opens=None, highs=None, lows=None, start='2026-01-01', freq='D'):
    idx = pd.date_range(start, periods=len(closes), freq=freq, tz='UTC')
    closes = np.asarray(closes, dtype=float)
    # Each day opens at the previous close unless a test sets a gap.
    opens = np.asarray(opens if opens is not None else np.concatenate([closes[:1], closes[:-1]]), dtype=float)
    return pd.DataFrame({
        'Open': opens,
        'High': np.asarray(highs if highs is not None else np.maximum(opens, closes), dtype=float),
        'Low': np.asarray(lows if lows is not None else np.minimum(opens, closes), dtype=float),
        'Close': closes,
        'Volume': 1000.0,
    }, index=idx)


def flat_sentiment(df):
    return pd.Series(0.0, index=df.index)


def bullish(i_from=0, i_to=10_000):
    return {i: {'signal': 'BUY', 'confidence': 80, 'probability_up': 0.8, 'model_fallback': False} for i in range(i_from, i_to)}


def run(df, signals, rule=None, start=25, cash=10_000, fee=0.0, slip=0.0):
    return backtest.simulate(df, signals, flat_sentiment(df), {**RULE, **(rule or {})}, start_index=start,
                             start_cash=cash, fee_pct=fee, slippage_pct=slip, symbol='X')


# --- Walk-forward AI never sees the future ---------------------------------------------------------

class FakeForecaster:
    buy_threshold, sell_threshold = 0.55, 0.45
    trained_until = []

    def __init__(self):
        self.feature_columns = ['close']

    @staticmethod
    def _build_features(df, sentiment):
        return pd.DataFrame({'close': df['Close'].astype(float)}, index=df.index)

    def train_model(self, df, sentiment):
        FakeForecaster.trained_until.append(df.index[-1])
        self.model = self

    def predict_proba(self, row):
        return np.array([[0.2, 0.8]])


def test_walk_forward_trains_only_on_bars_up_to_the_decision_day_and_retrains_on_schedule():
    FakeForecaster.trained_until = []
    df = bars(np.linspace(100, 120, 40))
    signals = backtest.walk_forward_signals(df, flat_sentiment(df), first_decision=20, retrain_every=5,
                                            make_forecaster=FakeForecaster)
    assert sorted(signals) == list(range(20, 40))
    assert FakeForecaster.trained_until == [df.index[i] for i in (20, 25, 30, 35)]
    assert signals[20] == {'signal': 'BUY', 'probability_up': 0.8, 'confidence': 80, 'model_fallback': False}


# --- Simulation --------------------------------------------------------------------------------------

def test_decision_at_close_fills_at_next_open_with_fee_and_slippage():
    closes = list(np.linspace(100, 110, 30))  # rising, so Trend Following agrees with the AI
    opens = list(closes)
    opens[25] = 110.0  # the open after the first decision
    df = bars(closes, opens=opens)
    out = run(df, {24: bullish()[24]}, fee=0.25, slip=0.05)
    buy = out['trades'][0]
    assert buy['date'] == df.index[25].date().isoformat() and buy['side'] == 'BUY'
    assert buy['price'] == pytest.approx(110 * 1.0005)
    assert buy['qty'] == pytest.approx(1000 * (1 - 0.0025) / (110 * 1.0005))


def test_trend_following_needs_the_one_month_trend_to_agree():
    down = bars(np.linspace(120, 100, 40))
    assert run(down, bullish())['trades'] == []
    up = bars(np.linspace(100, 120, 40))
    assert run(up, bullish(), cash=100_000)['metrics']['buys'] == 15  # one buy per test day


def test_buys_stop_when_cash_runs_out():
    up = bars(np.linspace(100, 120, 40))
    out = run(up, bullish(), cash=2500)
    assert out['metrics']['buys'] == 2 and out['metrics']['skipped_no_cash'] == 13


def test_stop_loss_gap_fills_at_the_open_and_counts_as_a_loss():
    closes = list(np.linspace(100, 110, 26)) + [110, 90, 90]
    opens = list(closes)
    opens[27] = 92.0  # gaps below the 5% stop (104.5)
    df = bars(closes, opens=opens)
    out = run(df, {24: bullish()[24]}, rule={'stop_loss_pct': 5})
    sell = out['trades'][-1]
    assert sell['side'] == 'SELL' and sell['reason'] == 'Stop-Loss' and sell['price'] == pytest.approx(92.0)
    assert out['metrics']['closed_trades'] == 1 and out['metrics']['win_rate'] == 0.0


def test_take_profit_and_stop_on_the_same_day_assumes_the_stop_came_first():
    closes = list(np.linspace(100, 110, 26)) + [110, 110]
    highs = list(closes)
    lows = list(closes)
    highs[26], lows[26] = 130.0, 90.0  # both the 10% target and the 5% stop are inside the day's range
    df = bars(closes, highs=highs, lows=lows)
    out = run(df, {24: bullish()[24]}, rule={'stop_loss_pct': 5, 'take_profit_pct': 10})
    assert [t['reason'] for t in out['trades']][-1] == 'Stop-Loss'


def test_take_profit_sells_at_the_target():
    closes = list(np.linspace(100, 110, 26)) + [125, 125]
    df = bars(closes)
    out = run(df, {24: bullish()[24]}, rule={'take_profit_pct': 10})
    sell = out['trades'][-1]
    entry = out['trades'][0]['price']
    assert sell['reason'] == 'Take-Profit' and sell['price'] == pytest.approx(entry * 1.10)
    assert sell['pl'] > 0 and out['metrics']['win_rate'] == 100.0


def test_trailing_stop_locks_in_gains_after_a_rise():
    closes = list(np.linspace(100, 110, 26)) + [120, 130, 120, 120]
    df = bars(closes)
    out = run(df, {24: bullish()[24]}, rule={'trailing_stop_pct': 5})
    sell = out['trades'][-1]
    assert sell['reason'] == 'Trailing Stop' and sell['price'] == pytest.approx(130 * 0.95)


def test_dca_buys_on_its_schedule_whatever_the_ai_says():
    df = bars([100.0] * 40)
    out = run(df, {}, rule={'strategy': 'Dollar-Cost Averaging', 'dca_interval_hours': 24 * 7})
    assert out['metrics']['buys'] == 3  # days 25, 32, 39


def test_buy_and_hold_and_drawdown():
    df = bars([100.0] * 25 + [100, 120, 60, 90])
    out = run(df, {})
    assert out['metrics']['hold_return_pct'] == pytest.approx(-10.0)
    assert out['metrics']['hold_max_drawdown_pct'] == pytest.approx(-50.0)
    assert out['metrics']['return_pct'] == 0.0 and out['metrics']['max_drawdown_pct'] == 0.0


def test_max_drawdown():
    assert backtest.max_drawdown_pct([100, 150, 75, 200, 180]) == -50.0
    assert backtest.max_drawdown_pct([100, 110]) == 0.0


def test_start_index_respects_the_training_window():
    df = bars([100.0] * 300)
    start, warnings = backtest.start_index_for(df, '1y')
    assert start == backtest.TRAIN_BARS and warnings
    with pytest.raises(ValueError):
        backtest.start_index_for(bars([100.0] * 190), '1y')


# --- Auto-tuning --------------------------------------------------------------------------------------

def tuner_fixture(monkeypatch, closes):
    df = bars(closes)
    signals = bullish(0, len(df))
    start = 30
    monkeypatch.setattr(backtest, '_prepared', lambda *a, **k: (df, flat_sentiment(df), signals, start, []))
    backtest._TUNE_CACHE.clear()
    return df


def test_tuner_keeps_a_stop_loss_on_and_reports_the_unseen_check_window(monkeypatch):
    # A steady zig-zag uptrend: small take-profits win often.
    closes = [100 * (1.004 ** i) * (1.03 if i % 4 == 0 else 1.0) for i in range(150)]
    tuner_fixture(monkeypatch, closes)
    result = backtest.tune_rule('ZZZ', trade_amount=1000)
    assert result['rule']['stop_loss_pct'] > 0
    assert result['tested'] == len(backtest.TUNE_STRATEGIES) * len(backtest.TUNE_STOP_LOSS) * len(backtest.TUNE_TRAILING) * len(backtest.TUNE_TAKE_PROFIT)
    assert result['tuning']['end'] < result['check']['start']
    assert result['full']['start'] == result['tuning']['start'] and result['full']['end'] == result['check']['end']
    if result['reached']:
        assert result['tuning']['win_rate'] >= result['target_win_rate'] and result['tuning']['return_pct'] > 0
        assert result['tuning']['trades'] >= backtest.MIN_TUNE_TRADES


def test_tuner_says_so_when_nothing_reaches_the_target(monkeypatch):
    # Steady decline: buying on a "bullish" outlook never works.
    tuner_fixture(monkeypatch, list(np.linspace(200, 100, 150)))
    result = backtest.tune_rule('DOWN', trade_amount=1000)
    assert result['reached'] is False and result['qualifying'] == 0
