import base64
import os
from decimal import Decimal

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from app.core import settrade
from app.core.brokers import ALPACA, SETTRADE, provider_for_symbol_hint


def test_login_signature_verifies_with_the_app_secret_key():
    # Settrade App Secrets are the base64 of a P-256 private scalar.
    scalar = int.from_bytes(os.urandom(32), 'big') % (2**255) + 1
    secret = base64.b64encode(scalar.to_bytes(32, 'big')).decode()
    content = 'app-id..1730000000000'
    signature = bytes.fromhex(settrade._sign(secret, content))
    public_key = ec.derive_private_key(scalar, ec.SECP256R1()).public_key()
    public_key.verify(signature, content.encode(), ec.ECDSA(hashes.SHA256()))  # raises if wrong


def test_invalid_secret_is_a_clear_error():
    with pytest.raises(Exception) as info:
        settrade._sign('not base64 !!', 'x')
    assert 'App Secret' in str(getattr(info.value, 'detail', info.value))


@pytest.mark.parametrize('price, expected', [
    ('1.234', '1.23'), ('3.33', '3.34'), ('7.12', '7.10'), ('12.34', '12.30'),
    ('34.13', '34.25'), ('150.3', '150.50'), ('250.4', '250.00'), ('401', '402.00'),
])
def test_round_to_set_tick(price, expected):
    assert settrade.round_to_tick(price) == Decimal(expected)


@pytest.mark.parametrize('amount, price, volume', [(10000, '34.25', 200), (3000, '34.25', 0), (100000, '1.5', 66600)])
def test_board_lot_volume(amount, price, volume):
    assert settrade.board_lot_volume(amount, price) == volume


def test_symbol_mapping():
    assert settrade.to_settrade_symbol('ptt.bk') == 'PTT'
    assert settrade.from_settrade_symbol('PTT') == 'PTT.BK'
    assert settrade.from_settrade_symbol('PTT.BK') == 'PTT.BK'


def test_normalize_account():
    account = settrade.normalize_account({'cashBalance': 1000000.0, 'lineAvailable': 950000.5, 'creditType': 'Cash Balance'})
    assert account == {'currency': 'THB', 'cash': 1000000.0, 'buying_power': 950000.5, 'credit_type': 'Cash Balance', 'can_buy': None, 'can_sell': None}


def test_normalize_positions_handles_list_and_wrapped_payloads():
    row = {'symbol': 'PTT', 'actualVolume': 300, 'averagePrice': 34.0, 'marketPrice': 35.0, 'marketValue': 10500.0, 'amount': 10200.0, 'profit': 300.0}
    for payload in ({'portfolioList': [row], 'totalPortfolio': {}}, [row]):
        [position] = settrade.normalize_positions(payload)
        assert position['symbol'] == 'PTT.BK'
        assert position['qty'] == 300 and position['avg_entry_price'] == 34.0
        assert position['market_value'] == 10500.0 and position['unrealized_pl'] == 300.0
        assert position['unrealized_plpc'] == pytest.approx(300 / 10200)
    # Missing value fields are derived, empty positions are skipped.
    [derived] = settrade.normalize_positions([{'symbol': 'KCE', 'actualVolume': 100, 'averagePrice': 40, 'marketPrice': 42},
                                              {'symbol': 'AOT', 'actualVolume': 0}])
    assert derived['market_value'] == 4200 and derived['unrealized_pl'] == 200


@pytest.mark.parametrize('order, state', [
    ({'orderNo': '1', 'vol': 100, 'matchQty': 100, 'price': 34.25, 'showStatus': 'Matched'}, 'filled'),
    ({'orderNo': '2', 'vol': 200, 'matchQty': 0, 'showStatus': 'Pending(OA)'}, 'pending'),
    ({'orderNo': '3', 'vol': 100, 'matchQty': 0, 'showStatus': 'Rejected'}, 'rejected'),
    ({'orderNo': '4', 'vol': 200, 'matchQty': 100, 'price': 34.0, 'showStatus': 'Cancelled'}, 'filled'),
    ({'orderNo': '5', 'vol': 100, 'matchQty': 0, 'showStatus': 'Expired'}, 'rejected'),
])
def test_order_outcome(order, state):
    outcome = settrade.order_outcome(order)
    assert outcome['state'] == state
    if state == 'filled':
        assert outcome['filled_qty'] == Decimal(str(order['matchQty']))


def test_normalize_trades_gives_stable_ids():
    rows = [{'orderNo': '9', 'symbol': 'PTT', 'side': 'Buy', 'qty': 100, 'price': 34.25, 'tradeTime': '10:15:30'},
            {'tradeNo': 'T1', 'orderNo': '10', 'symbol': 'KCE', 'side': 'S', 'volume': 200, 'price': 40}]
    first, second = settrade.normalize_trades(rows), settrade.normalize_trades(rows)
    assert [f['id'] for f in first] == [f['id'] for f in second]
    assert first[0]['symbol'] == 'PTT.BK' and first[0]['side'] == 'BUY'
    assert first[1]['id'] == 'settrade-T1' and first[1]['side'] == 'SELL' and first[1]['qty'] == 200


@pytest.mark.parametrize('symbol, provider', [
    ('PTT.BK', SETTRADE), ('kce.bk', SETTRADE),
    ('BTC-USD', ALPACA), ('BRK-B', ALPACA), ('HKDTHB=X', ALPACA), ('VOD.L', ALPACA),
])
def test_provider_hint_from_symbol_form(symbol, provider):
    assert provider_for_symbol_hint(symbol) == provider


def test_bare_ticker_needs_a_lookup():
    assert provider_for_symbol_hint('AAPL') is None  # could be US or a SET ticker without ".BK"


def test_sandbox_credentials_select_the_test_server():
    sandbox = settrade.SettradeCredentials('id', 'secret', 'SANDBOX', '', '123-E', '000000')
    live = settrade.SettradeCredentials('id', 'secret', '023', 'APP', '123-E', None)
    assert sandbox.is_sandbox and sandbox.broker_id == '098' and sandbox.base_url == settrade.SANDBOX_BASE_URL
    assert not live.is_sandbox and live.broker_id == '023' and live.base_url == settrade.PROD_BASE_URL
    assert sandbox.fingerprint != live.fingerprint
