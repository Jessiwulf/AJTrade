import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.api import portfolio
from app.core import trade_stats

T0 = datetime(2026, 9, 30, 2, 45, tzinfo=timezone.utc)


def fill(i, symbol, side, qty, price, minutes=0, at=None):
    return {'id': f'o{i}', 'symbol': symbol, 'side': side, 'quantity': Decimal(str(qty)), 'price': Decimal(str(price)),
            'at': at if at is not None else T0 + timedelta(minutes=minutes)}


def row(symbol, pl, at, side='SELL'):
    return {'trade_type': side, 'symbol': symbol, 'pl': None if pl is None else Decimal(str(pl)), 'created_at': at}


# --- Realized P/L ------------------------------------------------------------------------------

def test_many_buys_then_one_sell_is_one_closed_trade_on_average_cost():
    fills = [fill(i, 'ETH-USD', 'BUY', 1, 2600 + i * 10, minutes=i) for i in range(50)]  # avg 2845
    fills.append(fill(99, 'ETH-USD', 'SELL', 50, 2900, minutes=500))
    assert trade_stats.replay_realized_pl(fills) == {'o99': Decimal('2750.00')}


def test_order_of_input_does_not_matter_and_buys_go_first_on_a_tie():
    fills = [fill(2, 'TSLA', 'SELL', 1, 110, minutes=5), fill(1, 'TSLA', 'BUY', 1, 100, minutes=5)]
    assert trade_stats.replay_realized_pl(fills) == {'o2': Decimal('10.00')}


def test_sell_without_recorded_buys_has_no_pl_and_partial_cover_prices_covered_part():
    fills = [fill(1, 'AAPL', 'SELL', 5, 300), fill(2, 'META', 'BUY', 2, 500, 1), fill(3, 'META', 'SELL', 5, 550, 2)]
    assert trade_stats.replay_realized_pl(fills) == {'o1': None, 'o3': Decimal('100.00')}


def test_position_reopened_after_full_exit_starts_a_new_average():
    fills = [fill(1, 'X', 'BUY', 1, 100, 0), fill(2, 'X', 'SELL', 1, 90, 1), fill(3, 'X', 'BUY', 1, 200, 2),
             fill(4, 'X', 'SELL', 1, 210, 3)]
    assert trade_stats.replay_realized_pl(fills) == {'o2': Decimal('-10.00'), 'o4': Decimal('10.00')}


# --- Win rates ---------------------------------------------------------------------------------

def test_no_closed_trades_means_no_win_rate_not_zero_percent():
    stats = trade_stats.summarize(trade_stats.closed_trades([row('ETH-USD', None, T0, side='BUY')] * 100))
    assert stats == {'closed_trades': 0, 'winning_trades': 0, 'losing_trades': 0, 'win_rate': None, 'realized_pl': 0}


def test_unpriced_sells_and_buys_are_not_closed_trades_and_breakeven_is_not_a_win():
    rows = [row('A', 10, T0), row('A', -5, T0), row('A', 0, T0), row('A', None, T0), row('A', 50, T0, side='BUY')]
    stats = trade_stats.summarize(trade_stats.closed_trades(rows))
    assert stats['closed_trades'] == 3 and stats['winning_trades'] == 1 and stats['losing_trades'] == 1
    assert stats['win_rate'] == 33.33 and stats['realized_pl'] == 5.0


def test_by_symbol():
    rows = [row('eth-usd', -5416.94, T0), row('SOL-USD', -4061.52, T0), row('ETH-USD', 120, T0)]
    stats = trade_stats.by_symbol(trade_stats.closed_trades(rows))
    assert set(stats) == {'ETH-USD', 'SOL-USD'}
    assert stats['ETH-USD']['win_rate'] == 50.0 and stats['SOL-USD']['win_rate'] == 0.0


# --- Sync re-prices sells ------------------------------------------------------------------------

class FakeDB:
    def __init__(self, existing, bot_logs=()):
        self.existing = existing
        self.bot_logs = list(bot_logs)
        self.inserted = []
        self.updates = []

    async def fetch_all(self, query, values=None):
        if 'FROM trading_history' in query:
            return self.existing
        if 'FROM bot_execution_logs' in query:
            return self.bot_logs
        return []

    async def execute(self, query, values=None):
        if query.startswith('UPDATE trading_history SET pl'):
            self.updates.append(values)

    async def execute_many(self, query, values):
        self.inserted.extend(values)

    def transaction(self):
        class Tx:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *exc):
                return False
        return Tx()


def stored(i, symbol, side, qty, price, pl, at, notes=None):
    return {'id': f'row{i}', 'broker_order_id': f'o{i}', 'symbol': symbol, 'trade_type': side, 'quantity': Decimal(str(qty)),
            'price': Decimal(str(price)), 'pl': None if pl is None else Decimal(str(pl)), 'notes': notes, 'created_at': at}


def run_sync(monkeypatch, db, broker_fills):
    async def nothing(*a, **k):
        return None

    async def fills(*a, **k):
        return broker_fills

    async def owner_portfolio(*a, **k):
        return {'id': 'pid'}

    monkeypatch.setattr(portfolio, '_ensure_history_schema', nothing)
    monkeypatch.setattr(portfolio, '_broker_fills', fills)
    monkeypatch.setattr(portfolio, '_get_owner_portfolio', owner_portfolio)
    return asyncio.run(portfolio.sync_trade_history(db, 'owner', 'alpaca'))


def test_sync_fixes_a_sell_saved_without_pl_once_its_buy_is_recorded(monkeypatch):
    db = FakeDB([
        stored(1, 'TSLA', 'BUY', 2, 100, None, T0),
        stored(2, 'TSLA', 'SELL', 2, 120, None, T0 + timedelta(hours=1)),  # saved before the fix: no P/L
    ])
    assert run_sync(monkeypatch, db, []) == 0
    assert db.updates == [{'id': 'row2', 'pl': Decimal('40.00')}]


def test_sync_uses_the_bots_booked_pl_when_the_buys_are_missing(monkeypatch):
    db = FakeDB([], bot_logs=[
        {'broker_order_id': 'o7', 'signal_received': json.dumps({'side': 'SELL', 'estimated_pnl': -12.3})},
        {'broker_order_id': 'o8', 'signal_received': json.dumps({'side': 'BUY'})},
    ])
    new = {'broker_order_id': 'o7', 'symbol': 'SPY', 'side': 'SELL', 'quantity': Decimal('1'), 'price': Decimal('700'),
           'source': 'bot', 'notes': 'Alpaca order o7', 'created_at': T0}
    assert run_sync(monkeypatch, db, [new]) == 1
    assert db.inserted[0]['pl'] == Decimal('-12.30')


def test_sync_leaves_correct_rows_alone(monkeypatch):
    db = FakeDB([
        stored(1, 'ETH-USD', 'BUY', 2, 2600, None, T0),
        stored(2, 'ETH-USD', 'SELL', 2, 2400, -400, T0 + timedelta(days=28)),
    ])
    run_sync(monkeypatch, db, [])
    assert db.updates == []


def test_settrade_order_key():
    assert portfolio._settrade_order_key('Settrade order 1234') == 'st-1234'
    assert portfolio._settrade_order_key('Settrade order -') is None
    assert portfolio._settrade_order_key(None) is None
