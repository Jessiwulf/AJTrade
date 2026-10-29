import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.core import notifier

NOW = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)
TODAY = NOW.date().isoformat()
YESTERDAY = (NOW - timedelta(days=1)).date().isoformat()
LAST_WEEK = (NOW - timedelta(days=7)).date().isoformat()


def insight(**overrides):
    base = {
        'symbol': 'AAPL',
        'signal': 'HOLD',
        'confidence': 55,
        'latest_price': 250.0,
        'latest_sentiment_score': 0.05,
        'volatility_pct': 1.5,
        'price_history': [{'date': YESTERDAY, 'close': 100.0}, {'date': TODAY, 'close': 101.0}],
        'news_sentiment_daily': [],
    }
    base.update(overrides)
    return base


def headline(score, title='Apple faces antitrust ruling', url='https://news.example/a'):
    return {'title': title, 'source': 'Reuters', 'url': url, 'score': score}


def types(events):
    return sorted(e['event_type'] for e in events)


# --- Detection ------------------------------------------------------------------------------

def test_quiet_market_raises_nothing():
    assert notifier.detect_market_events('AAPL', insight(), now=NOW) == []


def test_severe_headline_with_sell_signal_is_panic_sell():
    events = notifier.detect_market_events(
        'AAPL',
        insight(signal='SELL', confidence=82, news_sentiment_daily=[
            {'date': TODAY, 'avg': -0.2, 'count': 3, 'headlines': [headline(-0.91)]},
        ]),
        now=NOW,
    )
    assert types(events) == ['panic_sell']
    event = events[0]
    assert event['severity'] == 'critical'
    assert event['symbol'] == 'AAPL'
    assert 'AI outlook is Bearish' in event['message']
    assert ('FinBERT score', '-0.91 (−1 to +1)') in event['fields']


def test_severe_headline_with_bearish_sentiment_is_panic_sell():
    events = notifier.detect_market_events(
        'AAPL',
        insight(latest_sentiment_score=-0.5, news_sentiment_daily=[
            {'date': YESTERDAY, 'avg': -0.5, 'count': 2, 'headlines': [headline(-0.8)]},
        ]),
        now=NOW,
    )
    assert types(events) == ['panic_sell']
    assert 'strongly bearish' in events[0]['message']


def test_severe_headline_without_confirmation_is_negative_news_warning():
    events = notifier.detect_market_events(
        'AAPL',
        insight(news_sentiment_daily=[{'date': TODAY, 'avg': -0.1, 'count': 4, 'headlines': [headline(-0.8)]}]),
        now=NOW,
    )
    assert types(events) == ['negative_news']
    assert events[0]['severity'] == 'warning'


def test_mildly_negative_or_old_headlines_are_ignored():
    events = notifier.detect_market_events(
        'AAPL',
        insight(signal='SELL', news_sentiment_daily=[
            {'date': LAST_WEEK, 'avg': -0.9, 'count': 1, 'headlines': [headline(-0.95)]},
            {'date': TODAY, 'avg': -0.3, 'count': 2, 'headlines': [headline(-0.6, url='https://news.example/b')]},
        ]),
        now=NOW,
    )
    assert 'panic_sell' not in types(events) and 'negative_news' not in types(events)


def test_same_catalyst_keeps_the_same_event_key():
    def run(**extra):
        return notifier.detect_market_events(
            'AAPL',
            insight(signal='SELL', news_sentiment_daily=[
                {'date': TODAY, 'avg': -0.4, 'count': 3, 'headlines': [headline(-0.9)]},
            ], **extra),
            now=NOW,
        )[0]['event_key']

    # Re-evaluated on later refreshes with different confidence: still one alert.
    assert run(confidence=71) == run(confidence=93)


def test_price_shock_uses_volatility_threshold():
    # 4% move: below the 5% floor -> nothing.
    calm = insight(volatility_pct=1.0, price_history=[{'date': YESTERDAY, 'close': 100}, {'date': TODAY, 'close': 96}])
    assert notifier.detect_market_events('AAPL', calm, now=NOW) == []

    # 7% drop with 1.5% typical move (threshold max(5, 4.5) = 5%) -> critical shock.
    crash = insight(volatility_pct=1.5, price_history=[{'date': YESTERDAY, 'close': 100}, {'date': TODAY, 'close': 93}])
    events = notifier.detect_market_events('AAPL', crash, now=NOW)
    assert types(events) == ['price_shock']
    assert events[0]['severity'] == 'critical'
    assert events[0]['event_key'] == f'price_shock:AAPL:{TODAY}:down'

    # 7% move on a volatile asset (3% typical -> 9% threshold) is normal for it.
    volatile = insight(volatility_pct=3.0, price_history=[{'date': YESTERDAY, 'close': 100}, {'date': TODAY, 'close': 107}])
    assert notifier.detect_market_events('AAPL', volatile, now=NOW) == []


def test_price_shock_ignores_stale_prices():
    old = insight(price_history=[{'date': LAST_WEEK, 'close': 100}, {'date': LAST_WEEK, 'close': 80}])
    assert notifier.detect_market_events('AAPL', old, now=NOW) == []


def test_sentiment_collapse():
    events = notifier.detect_market_events(
        'AAPL',
        insight(news_sentiment_daily=[
            {'date': YESTERDAY, 'avg': 0.3, 'count': 3, 'headlines': []},
            {'date': TODAY, 'avg': -0.45, 'count': 3, 'headlines': []},
        ]),
        now=NOW,
    )
    assert types(events) == ['sentiment_shock']

    single_article = notifier.detect_market_events(
        'AAPL',
        insight(news_sentiment_daily=[
            {'date': YESTERDAY, 'avg': 0.3, 'count': 3, 'headlines': []},
            {'date': TODAY, 'avg': -0.45, 'count': 1, 'headlines': []},
        ]),
        now=NOW,
    )
    assert single_article == []


def test_malformed_insight_does_not_raise():
    bad = {'news_sentiment_daily': [{'date': TODAY, 'headlines': [{'score': 'n/a'}]}, 'junk'], 'price_history': [{'close': None}]}
    assert notifier.detect_market_events('AAPL', bad, now=NOW) == []


# --- Trade events ---------------------------------------------------------------------------

def trade_row(payload, **overrides):
    import json

    row = {
        'id': 'log-1',
        'owner_id': 'owner-1',
        'timestamp': NOW,
        'asset_symbol': 'AAPL',
        'signal_received': json.dumps(payload),
        'execution_price': 342.1,
        'broker_order_id': 'ord-9',
    }
    row.update(overrides)
    return row


def test_buy_trade_event():
    event = notifier._build_trade_event(trade_row({
        'side': 'BUY', 'requested_amount': 1000, 'confidence': 0.82,
        'metadata': {'strategy': 'Trend Following', 'strategy_reason': 'Uptrend confirmed'},
    }), 'paper')
    assert event['event_type'] == 'trade_buy'
    assert event['event_key'] == 'trade:log-1'
    assert event['title'] == 'BUY executed: AAPL @ $342.10'
    fields = dict(event['fields'])
    assert fields['Order amount (requested)'] == '$1,000.00'
    assert fields['AI confidence'] == '82%'
    assert fields['Mode'] == 'Alpaca paper trading'
    assert 'Realized P/L' not in fields


def test_stop_loss_exit_event_reports_realized_loss():
    event = notifier._build_trade_event(trade_row({
        'side': 'SELL', 'requested_amount': 970, 'estimated_pnl': -31.5,
        'metadata': {'source': 'bot-risk-exit', 'trigger': 'Stop-loss'},
    }), 'paper')
    assert event['event_type'] == 'trade_sell'
    assert event['title'].startswith('Stop-loss exit executed: SELL AAPL')
    assert dict(event['fields'])['Realized P/L'] == '−$31.50'


# --- Webhook validation ---------------------------------------------------------------------

@pytest.mark.parametrize('url', [
    'https://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz_-0123',
    'https://discordapp.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz_-0123',
    'https://canary.discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz_-0123',
])
def test_valid_discord_webhooks(url):
    assert notifier.validate_discord_webhook(f'  {url} ') == url
    assert 'abcdefghij' not in notifier.mask_webhook(url)


@pytest.mark.parametrize('url', [
    'http://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz',
    'https://evil.example/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz',
    'https://discord.com.evil.example/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz',
    'https://discord.com/api/webhooks/123456789012345678/short',
    'https://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz?x=1',
    '',
])
def test_invalid_discord_webhooks(url):
    with pytest.raises(ValueError):
        notifier.validate_discord_webhook(url)


def test_email_rendering_escapes_html():
    event = {**notifier.build_test_event(), 'title': '<script>x</script>', 'fields': [('A&B', '<b>1</b>')]}
    text, html = notifier._render_email(event)
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert 'A&amp;B' in html and '<b>1</b>' in text


# --- Dispatch channel selection -------------------------------------------------------------

class FakeDB:
    def __init__(self):
        self.keys = set()
        self.updates = []

    async def fetch_val(self, query, values=None):
        if 'INSERT INTO notification_log' in query:
            if values['event_key'] in self.keys:
                return None
            self.keys.add(values['event_key'])
            return f"id-{len(self.keys)}"
        return None

    async def execute(self, query, values=None):
        self.updates.append(values)


@pytest.fixture
def harness(monkeypatch):
    db = FakeDB()
    sent = []
    state = {'prefs': None}

    async def fake_ensure():
        return None

    async def fake_prefs(owner_id):
        return state['prefs']

    async def fake_email(to, event):
        sent.append(('email', to, event['event_key']))
        return {'status': 'sent'}

    async def fake_discord(url, event):
        sent.append(('discord', url, event['event_key']))
        return {'status': 'sent'}

    monkeypatch.setattr(notifier, 'ensure_schema', fake_ensure)
    monkeypatch.setattr(notifier, 'load_preferences', fake_prefs)
    monkeypatch.setattr(notifier, 'get_database', lambda: db)
    monkeypatch.setattr(notifier, '_send_email', fake_email)
    monkeypatch.setattr(notifier, '_send_discord', fake_discord)
    monkeypatch.setattr(notifier, '_decrypt_webhook', lambda owner, prefs: 'https://discord.com/api/webhooks/1/x')
    return state, sent, db


def prefs(email_enabled=True, discord_enabled=False, **events):
    return {
        'email': 'member@example.com',
        'email_enabled': email_enabled,
        'discord_enabled': discord_enabled,
        'discord_webhook_enc': b'x' if discord_enabled else None,
        'events': {**notifier.DEFAULT_EVENTS, **events},
    }


EVENT = {'event_type': 'panic_sell', 'event_key': 'panic_sell:AAPL:abc', 'severity': 'critical',
         'symbol': 'AAPL', 'title': 'Panic-sell warning: AAPL', 'message': 'm', 'fields': []}


@pytest.mark.parametrize('email_on, discord_on, expected', [
    (True, False, {'email'}),
    (False, True, {'discord'}),
    (True, True, {'email', 'discord'}),
])
def test_dispatch_uses_only_active_channels(harness, email_on, discord_on, expected):
    state, sent, _ = harness
    state['prefs'] = prefs(email_enabled=email_on, discord_enabled=discord_on)
    result = asyncio.run(notifier.dispatch('owner-1', EVENT))
    assert result['status'] == 'sent'
    assert {channel for channel, _, _ in sent} == expected


def test_dispatch_all_off_sends_nothing(harness):
    state, sent, db = harness
    state['prefs'] = prefs(email_enabled=False, discord_enabled=False)
    assert asyncio.run(notifier.dispatch('owner-1', EVENT))['reason'] == 'no_active_channel'
    assert sent == [] and db.keys == set()


def test_dispatch_respects_event_toggle(harness):
    state, sent, _ = harness
    state['prefs'] = prefs(panic_sell=False)
    assert asyncio.run(notifier.dispatch('owner-1', EVENT))['reason'] == 'event_disabled'
    assert sent == []


def test_dispatch_deduplicates_event_keys(harness):
    state, sent, _ = harness
    state['prefs'] = prefs()
    first = asyncio.run(notifier.dispatch('owner-1', EVENT))
    second = asyncio.run(notifier.dispatch('owner-1', EVENT))
    assert first['status'] == 'sent' and second['status'] == 'duplicate'
    assert len(sent) == 1


def test_test_send_bypasses_toggle_and_dedupe(harness):
    state, sent, db = harness
    state['prefs'] = prefs(discord_enabled=True, panic_sell=False)
    event = notifier.build_test_event()
    asyncio.run(notifier.dispatch('owner-1', event, force=True))
    asyncio.run(notifier.dispatch('owner-1', event, force=True))
    assert len(sent) == 4 and db.keys == set()
