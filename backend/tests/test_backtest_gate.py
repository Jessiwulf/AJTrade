import asyncio

from app.core import backtest_store as store

RULE = {'strategy': 'Trend Following', 'stop_loss_pct': 5, 'trailing_stop_pct': 0, 'take_profit_pct': 0, 'dca_interval_hours': 24}


def test_rule_key_changes_with_any_setting_that_changes_the_backtest():
    base = store.rule_key(RULE)
    assert store.rule_key({**RULE, 'stop_loss_pct': 5.0}) == base
    assert store.rule_key({**RULE, 'take_profit_pct': 10}) != base
    assert store.rule_key({**RULE, 'strategy': 'Mean Reversion'}) != base


def test_gate_needs_the_win_rate_and_enough_trades():
    assert store.passes({'win_rate': 60.0, 'trades': 3})
    assert not store.passes({'win_rate': 59.9, 'trades': 20})
    assert not store.passes({'win_rate': 100.0, 'trades': 2})  # 2 of 2 says little
    assert not store.passes({'win_rate': None, 'trades': 0})
    assert not store.passes(None)


def gate(monkeypatch, saved, rule=RULE):
    async def fake_result(owner, symbol):
        return saved

    monkeypatch.setattr(store, 'rule_result', fake_result)
    return asyncio.run(store.gate_status('u1', 'AAPL', rule))['state']


def test_gate_states(monkeypatch):
    key = store.rule_key(RULE)
    assert gate(monkeypatch, None) == 'pending'
    assert gate(monkeypatch, {'rule_key': 'old|settings', 'win_rate': 90, 'trades': 10}) == 'pending'
    assert gate(monkeypatch, {'rule_key': key, 'win_rate': 86, 'trades': 14}) == 'pass'
    assert gate(monkeypatch, {'rule_key': key, 'win_rate': 53, 'trades': 15}) == 'fail'
    assert gate(monkeypatch, {'rule_key': key, 'win_rate': 66.7, 'trades': 3}) == 'pass'
    # DCA ignores the AI outlook, so it is never gated.
    assert gate(monkeypatch, None, {**RULE, 'strategy': 'Dollar-Cost Averaging'}) == 'off'
    # The member switched the check off for this asset.
    assert gate(monkeypatch, {'rule_key': key, 'win_rate': 10, 'trades': 9}, {**RULE, 'backtest_check': False}) == 'disabled'
