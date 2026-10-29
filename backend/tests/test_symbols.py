import pytest

from app.api.market import yahoo_symbol_candidates
from app.api.watchlist import _SYMBOL_RE, _normalize_symbol
from app.core.alpaca import from_alpaca_symbol, to_alpaca_symbol


@pytest.mark.parametrize('typed, expected', [
    # Share classes as written by exchanges / stockanalysis.com -> Yahoo dash form tried second.
    ('BRK.B', ['BRK.B', 'BRK-B']),
    ('brk.b', ['BRK.B', 'BRK-B']),
    ('BF.B', ['BF.B', 'BF-B']),
    ('MOG.A', ['MOG.A', 'MOG-A']),
    ('BRK-B', ['BRK-B']),
    # Preferreds: ALL.PRB / ALL-PRB -> ALL-PB
    ('ALL.PRB', ['ALL.PRB', 'ALL-PB']),
    ('ALL-PRB', ['ALL-PRB', 'ALL-PB']),
    # Other formats are left alone (exchange suffixes stay as typed first).
    ('AAPL', ['AAPL']),
    ('PTT.BK', ['PTT.BK']),
    ('BTC-USD', ['BTC-USD']),
    ('HKDTHB=X', ['HKDTHB=X']),
])
def test_yahoo_symbol_candidates(typed, expected):
    assert yahoo_symbol_candidates(typed) == expected


@pytest.mark.parametrize('typed', ['BRK.B', 'brk/b', 'ALL.PRB', 'PTT.BK', 'BTC/USD', 'HKDTHB=X', 'GOOGL'])
def test_watchlist_accepts_common_formats(typed):
    assert _SYMBOL_RE.match(_normalize_symbol(typed))


@pytest.mark.parametrize('app, alpaca', [
    ('BRK-B', 'BRK.B'),
    ('BF-A', 'BF.A'),
    ('AAPL', 'AAPL'),
    ('BTC-USD', 'BTC/USD'),
    ('PTT.BK', 'PTT.BK'),
])
def test_alpaca_symbol_mapping(app, alpaca):
    assert to_alpaca_symbol(app) == alpaca


@pytest.mark.parametrize('alpaca, app, asset_class', [
    ('BRK.B', 'BRK-B', 'us_equity'),
    ('AAPL', 'AAPL', 'us_equity'),
    ('BTC/USD', 'BTC-USD', 'crypto'),
    ('ETHUSD', 'ETH-USD', 'crypto'),
])
def test_from_alpaca_symbol(alpaca, app, asset_class):
    assert from_alpaca_symbol(alpaca, asset_class) == app
