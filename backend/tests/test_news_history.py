from datetime import date, datetime, timedelta, timezone

import pytest

from app.core import news_history as nh

NOW = datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc)
TODAY = NOW.date()


# --- Which symbols Alpaca has news for --------------------------------------------------------

@pytest.mark.parametrize('symbol, expected', [
    ('AAPL', 'AAPL'),
    ('spy', 'SPY'),
    ('BRK-B', 'BRK.B'),
    ('BTC-USD', 'BTCUSD'),
    ('ETH-USD', 'ETHUSD'),
    ('KCE.BK', None),       # Thai stock
    ('HKDTHB=X', None),     # currency
    ('XAU-USD', None),      # gold: not an Alpaca coin
    ('^GSPC', None),        # index
    ('VOD.L', None),        # London
    ('', None),
])
def test_news_symbol(symbol, expected):
    assert nh.news_symbol(symbol) == expected


# --- Planning ---------------------------------------------------------------------------------

def test_a_new_symbol_needs_every_day_newest_first():
    days = nh.days_to_fetch({}, now=NOW, days=30)
    assert len(days) == 30
    assert days[0] == TODAY and days[-1] == TODAY - timedelta(days=29)


def test_finished_days_are_fetched_once():
    two_days_ago = TODAY - timedelta(days=2)
    fetched = {two_days_ago: NOW - timedelta(hours=1)}  # fetched long after that day ended
    assert two_days_ago not in nh.days_to_fetch(fetched, now=NOW, days=30)


def test_today_is_refetched_after_the_refresh_interval():
    recent = {TODAY: NOW - timedelta(minutes=5)}
    stale = {TODAY: NOW - timedelta(minutes=nh.NEWS_HISTORY_REFRESH_MINUTES + 1)}
    assert TODAY not in nh.days_to_fetch(recent, now=NOW, days=30)
    assert TODAY in nh.days_to_fetch(stale, now=NOW, days=30)


def test_yesterday_fetched_just_after_midnight_is_not_final():
    yesterday = TODAY - timedelta(days=1)
    just_after_midnight = datetime.combine(TODAY, datetime.min.time(), tzinfo=timezone.utc) + timedelta(minutes=10)
    later = just_after_midnight + timedelta(minutes=nh.NEWS_HISTORY_REFRESH_MINUTES + 1)
    assert yesterday in nh.days_to_fetch({yesterday: just_after_midnight}, now=later, days=30)


def test_tasks_fill_from_today_backwards_for_all_symbols():
    d0, d1 = TODAY, TODAY - timedelta(days=1)
    tasks = nh.plan_tasks({'TSLA': [d0, d1], 'AAPL': [d0, d1]})
    assert tasks == [('AAPL', d0), ('TSLA', d0), ('AAPL', d1), ('TSLA', d1)]


# --- Summaries --------------------------------------------------------------------------------

def article(day, score, title='Headline', url=None):
    return {'day': day, 'score': score, 'title': title, 'source': 'Benzinga', 'url': url or f'https://x/{title}/{score}'}


def test_summarize_days_counts_and_strongest_headlines():
    out = nh.summarize_days([
        article('2026-10-27', 0.8, 'Beat'),
        article('2026-10-27', -0.4, 'Probe'),
        article('2026-10-27', 0.05, 'Flat'),
        article('2026-10-27', 0.2, 'Upgrade'),
        article('2026-10-26', -0.6, 'Miss'),
    ])
    assert [d['date'] for d in out] == ['2026-10-26', '2026-10-27']
    day = out[1]
    assert day['count'] == 4 and day['positive'] == 2 and day['negative'] == 1 and day['neutral'] == 1
    assert day['avg'] == pytest.approx((0.8 - 0.4 + 0.05 + 0.2) / 4, abs=1e-4)
    assert [h['title'] for h in day['headlines']] == ['Beat', 'Probe', 'Upgrade']


def test_merge_weights_by_article_count_and_dedupes_headlines():
    shared = {'title': 'Same story', 'url': 'https://x/same', 'score': 0.5, 'source': 'A'}
    a = [{'date': '2026-10-27', 'avg': 0.5, 'count': 3, 'positive': 3, 'negative': 0, 'neutral': 0, 'headlines': [shared]}]
    b = [
        {'date': '2026-10-27', 'avg': -0.5, 'count': 1, 'positive': 0, 'negative': 1, 'neutral': 0, 'headlines': [shared]},
        {'date': '2026-10-20', 'avg': 0.1, 'count': 2, 'positive': 1, 'negative': 0, 'neutral': 1, 'headlines': []},
    ]
    out = nh.merge_daily(a, b)
    assert [d['date'] for d in out] == ['2026-10-20', '2026-10-27']
    day = out[1]
    assert day['count'] == 4 and day['avg'] == pytest.approx(0.25)
    assert day['positive'] == 3 and day['negative'] == 1
    assert len(day['headlines']) == 1


def test_merge_keeps_one_source_unchanged():
    a = nh.summarize_days([article('2026-10-27', 0.3)])
    assert nh.merge_daily(a, []) == a


def test_recent_average_uses_the_last_week_only():
    daily = [
        {'date': (TODAY - timedelta(days=20)).isoformat(), 'avg': -0.9, 'count': 10},
        {'date': (TODAY - timedelta(days=2)).isoformat(), 'avg': 0.4, 'count': 3, 'positive': 3},
        {'date': TODAY.isoformat(), 'avg': 0.0, 'count': 1, 'neutral': 1},
    ]
    recent = nh.recent_average(daily, now=NOW)
    assert recent['total_articles'] == 4
    assert recent['avg_sentiment'] == pytest.approx(0.3)
    assert recent['date'] == TODAY.isoformat()
    assert nh.recent_average(daily[:1], now=NOW) is None


def test_article_text_strips_html_from_the_summary():
    assert nh._article_text({'headline': 'Apple rises', 'summary': '<p>Strong <b>iPhone</b> sales</p>'}) == 'Apple rises. Strong iPhone sales'
    assert nh._article_text({'headline': 'Only a headline', 'summary': ''}) == 'Only a headline'
