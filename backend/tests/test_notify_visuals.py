import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.core import notifier, notify_visuals as visuals

NOW = datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc)
TODAY = NOW.date()


def closes(values):
    return [{'date': (TODAY - timedelta(days=len(values) - 1 - i)).isoformat(), 'close': v} for i, v in enumerate(values)]


def day(offset, avg, headlines=()):
    return {'date': (TODAY - timedelta(days=offset)).isoformat(), 'avg': avg, 'count': 3,
            'positive': 1, 'negative': 1, 'neutral': 1, 'headlines': list(headlines)}


# --- Building blocks --------------------------------------------------------------------------

@pytest.mark.parametrize('value, expected', [(-1, 0), (-0.95, 0), (0, 10), (0.99, 19), (1, 19), (5, 19), (-5, 0)])
def test_gauge_index_on_sentiment_scale(value, expected):
    assert visuals.gauge_index(value, -1, 1) == expected


def test_sparkline_scales_to_range_and_keeps_gaps():
    assert visuals.sparkline([1, 2, None, 8]) == '▁▂ █'
    assert visuals.sparkline([5, 5]) == '▄▄'
    assert visuals.sparkline([]) == ''


def test_sentiment_points_cover_every_day_with_gaps():
    points = visuals.sentiment_points([day(0, -0.6), day(2, 0.3)], now=NOW, days=4)
    assert [p[0] for p in points] == [(TODAY - timedelta(days=d)).isoformat() for d in (3, 2, 1, 0)]
    assert [p[1] for p in points] == [None, 0.3, None, -0.6]


# --- Visual specs ------------------------------------------------------------------------------

def test_buy_visual_has_price_chart_with_fill_marker():
    v = visuals.trade_visual(side='BUY', symbol='TSLA', price=446.12, pnl=None, trigger=None, history=closes([400, 420, 440]))
    assert v['tone'] == 'positive' and 'BUY' in v['headline'] and '$446.12' in v['headline']
    assert v['chart']['kind'] == 'price' and v['chart']['marker'] == {'value': 446.12, 'label': 'BUY $446.12', 'side': 'BUY'}


def test_losing_exit_leads_with_realized_pnl():
    v = visuals.trade_visual(side='SELL', symbol='TSLA', price=431.5, pnl=-43.86, trigger='Stop-loss', history=[])
    assert v['headline'] == '−$43.86' and v['tone'] == 'negative'
    assert 'Stop-loss' in v['headline_label']
    assert 'chart' not in v  # no price history saved: no empty chart


def test_market_events_carry_visuals():
    insight = {
        'symbol': 'TSLA', 'signal': 'SELL', 'confidence': 74, 'latest_price': 412.3, 'latest_sentiment_score': -0.58,
        'volatility_pct': 2.0, 'price_history': closes([440, 444, 412.3]),
        'news_sentiment_daily': [day(1, 0.45), day(0, -0.4, [{'title': 'Recall', 'url': 'u', 'score': -0.93}])],
    }
    events = {e['event_type']: e for e in notifier.detect_market_events('TSLA', insight, now=NOW)}
    assert set(events) == {'panic_sell', 'price_shock', 'sentiment_shock'}
    panic = events['panic_sell']['visual']
    assert panic['headline'] == '−0.93' and panic['gauge'] == {'kind': 'sentiment', 'label': 'Overall news sentiment', 'value': -0.58}
    assert panic['chart']['points'][-1] == (TODAY.isoformat(), -0.4)
    shock = events['price_shock']['visual']
    assert shock['headline'].startswith('−7.') and shock['gauge']['kind'] == 'ratio' and shock['chart']['highlight_last']
    assert events['sentiment_shock']['visual']['headline'] == '+0.45 → −0.40'


def test_test_alert_previews_the_visuals():
    v = notifier.build_test_event()['visual']
    assert v['gauge']['kind'] == 'sentiment' and v['chart']['kind'] == 'price'


# --- Rendering ---------------------------------------------------------------------------------

def test_chart_png_renders_when_matplotlib_is_available():
    pytest.importorskip('matplotlib')
    for v in (
        visuals.trade_visual(side='BUY', symbol='X', price=3, pnl=None, trigger=None, history=closes([1, 2, 3])),
        visuals.news_visual(symbol='X', worst_score=-0.9, latest_sentiment=None, daily=[day(0, -0.5), day(1, 0)], critical=True, now=NOW),
    ):
        png = visuals.render_chart_png(v)
        assert png and png[:8] == b'\x89PNG\r\n\x1a\n'


def test_no_chart_for_too_little_data():
    assert visuals.render_chart_png({'chart': {'kind': 'price', 'points': [('2026-10-28', 1.0)]}}) is None
    assert visuals.render_chart_png({'headline': 'x'}) is None
    assert visuals.render_chart_png(None) is None


def test_email_html_shows_visual_and_inline_chart():
    event = notifier.build_test_event()
    _, html = notifier._render_email(event, chart_cid='chart@ajtrade')
    assert 'Sample news sentiment' in html and 'cid:chart@ajtrade' in html
    _, html_no_image = notifier._render_email(event)
    assert 'cid:' not in html_no_image and 'Sample news sentiment' in html_no_image


def test_email_visual_escapes_html():
    event = {**notifier.build_test_event(), 'visual': {'headline': '<b>x</b>', 'headline_label': '<script>', 'tone': 'neutral'}}
    _, html = notifier._render_email(event)
    assert '<script>' not in html and '&lt;b&gt;x&lt;/b&gt;' in html


def test_discord_description_starts_with_the_visual():
    event = notifier.build_test_event()
    description = notifier._render_discord_embed(event)['description']
    assert description.startswith('## Sample')
    assert '🔴 `' in description and event['message'] in description


def test_email_message_carries_the_chart_as_inline_image(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self, context=None):
            pass

        def login(self, user, password):
            pass

        def send_message(self, message):
            sent['message'] = message

    monkeypatch.setenv('SMTP_HOST', 'smtp.example.com')
    monkeypatch.setenv('SMTP_FROM', 'alerts@example.com')
    monkeypatch.setattr(notifier.smtplib, 'SMTP', FakeSMTP)
    notifier._send_email_sync('member@example.com', 'subject', 'text', '<img src="cid:chart@ajtrade">', b'\x89PNGdata')
    images = [part for part in sent['message'].walk() if part.get_content_type() == 'image/png']
    assert len(images) == 1 and images[0]['Content-ID'] == '<chart@ajtrade>'
    assert images[0].get_payload(decode=True) == b'\x89PNGdata'


def test_discord_uploads_the_chart_and_embeds_it(monkeypatch):
    seen = {}

    def handler(request):
        seen['content_type'] = request.headers['content-type']
        seen['body'] = request.read()
        return httpx.Response(200, json={'id': '1'})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(notifier.httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    event = {**notifier.build_test_event(), '_chart_png': b'\x89PNGdata'}
    result = asyncio.run(notifier._send_discord('https://discord.com/api/webhooks/1/x', event))
    assert result == {'status': 'sent'}
    assert seen['content_type'].startswith('multipart/form-data')
    assert b'attachment://chart.png' in seen['body'] and b'\x89PNGdata' in seen['body']


def test_discord_without_chart_posts_json(monkeypatch):
    seen = {}

    def handler(request):
        seen['payload'] = json.loads(request.read())
        return httpx.Response(200, json={'id': '1'})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(notifier.httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    event = {**notifier.build_test_event(), '_chart_png': None}
    assert asyncio.run(notifier._send_discord('https://discord.com/api/webhooks/1/x', event)) == {'status': 'sent'}
    assert 'image' not in seen['payload']['embeds'][0]


def test_dispatch_renders_the_chart_once_for_all_channels(monkeypatch):
    calls = []
    received = []

    def fake_render(visual):
        calls.append(visual)
        return b'png'

    async def fake_send(target, event):
        received.append(event.get('_chart_png'))
        return {'status': 'sent'}

    async def nothing(*args, **kwargs):
        return None

    async def prefs(owner_id):
        return {'email': 'a@b.c', 'email_enabled': True, 'discord_enabled': True, 'discord_webhook_enc': b'x', 'events': notifier.DEFAULT_EVENTS}

    monkeypatch.setattr(notifier.visuals, 'render_chart_png', fake_render)
    monkeypatch.setattr(notifier, 'ensure_schema', nothing)
    monkeypatch.setattr(notifier, 'load_preferences', prefs)
    monkeypatch.setattr(notifier, '_resolve_email', lambda owner, p: asyncio.sleep(0, result='a@b.c'))
    monkeypatch.setattr(notifier, '_decrypt_webhook', lambda owner, p: 'https://discord.com/api/webhooks/1/x')
    monkeypatch.setattr(notifier, '_send_email', fake_send)
    monkeypatch.setattr(notifier, '_send_discord', fake_send)
    result = asyncio.run(notifier.dispatch('owner', notifier.build_test_event(), force=True))
    assert result['status'] == 'sent'
    assert len(calls) == 1 and received == [b'png', b'png']
