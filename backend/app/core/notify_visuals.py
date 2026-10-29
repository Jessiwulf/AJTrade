"""At-a-glance visuals for alert emails and Discord messages (Feature #8).

Every alert can carry a `visual` spec, built from data the alert already has:

    {
      'tone': 'positive' | 'negative' | 'warning' | 'neutral',
      'headline': '-7.42%', 'headline_label': 'Move vs previous close',
      'gauge': {'kind': 'sentiment', 'label': ..., 'value': -0.91}               # -1 .. +1
             | {'kind': 'ratio', 'label': ..., 'value': 7.4, 'threshold': 5.0, 'typical': 1.8, 'unit': '%'},
      'chart': {'kind': 'price' | 'sentiment', 'title': ..., 'points': [(label, value or None), ...],
                'marker': {'value': 412.3, 'label': 'BUY $412.30', 'side': 'BUY'}, 'highlight_last': bool},
    }

It is rendered three ways:
- a PNG chart (matplotlib, when installed), inlined in the email and attached to the Discord embed;
- email-safe HTML (table cells only, so it shows even when the mail client blocks images);
- Discord text (monospace gauge, sparkline and coloured day squares).
"""
from __future__ import annotations

import io
import logging
from datetime import date, datetime, timedelta, timezone
from html import escape
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger('ajtrade.notifier')

GREEN = '#16a34a'
RED = '#dc2626'
AMBER = '#f59e0b'
NAVY = '#1e3a8a'
MUTED = '#64748b'
TRACK = '#e2e8f0'
TONE_COLORS = {'positive': GREEN, 'negative': RED, 'warning': AMBER, 'neutral': NAVY}

GAUGE_CELLS = 20
SPARK_CHARS = '▁▂▃▄▅▆▇█'
SENTIMENT_DAYS = 14

Point = Tuple[str, Optional[float]]


# --- Shared helpers ---------------------------------------------------------------------------
def sentiment_word(score: float) -> str:
    """Same labels as the app (Analytics heatmap)."""
    if score >= 0.5:
        return 'Very Bullish'
    if score >= 0.1:
        return 'Bullish'
    if score > -0.1:
        return 'Neutral'
    if score >= -0.5:
        return 'Bearish'
    return 'Very Bearish'


def _signed(value: float, digits: int = 2) -> str:
    return f"{'+' if value > 0 else '−' if value < 0 else ''}{abs(value):.{digits}f}"


def _money(value: float) -> str:
    return f'${value:,.2f}'


def _short_date(label: str) -> str:
    text = str(label)[:10]
    return text[5:] if len(text) == 10 and text[4] == '-' else text


def gauge_index(value: float, low: float, high: float, cells: int = GAUGE_CELLS) -> int:
    """Which of `cells` equal slots `value` falls in on a low..high scale."""
    if high <= low:
        return 0
    t = (min(max(float(value), low), high) - low) / (high - low)
    return min(int(t * cells), cells - 1)


def ratio_scale_max(gauge: Dict[str, Any]) -> float:
    value = abs(float(gauge.get('value') or 0))
    threshold = float(gauge.get('threshold') or 0)
    return max(value * 1.15, threshold * 1.6, 1e-9)


def sentiment_points(daily: Sequence[Dict[str, Any]], *, now: Optional[datetime] = None, days: int = SENTIMENT_DAYS) -> List[Point]:
    """Last `days` UTC days of a daily sentiment summary; days without news are None."""
    today = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()
    by_day = {}
    for entry in daily or []:
        try:
            by_day[str(entry['date'])[:10]] = float(entry['avg'])
        except (KeyError, TypeError, ValueError):
            continue
    out: List[Point] = []
    for offset in range(days - 1, -1, -1):
        key = (today - timedelta(days=offset)).isoformat()
        out.append((key, by_day.get(key)))
    return out


def price_points(history: Sequence[Dict[str, Any]], limit: int = 30) -> List[Point]:
    out: List[Point] = []
    for item in list(history or [])[-limit:]:
        try:
            out.append((str(item.get('date'))[:10], float(item['close'])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def sparkline(values: Sequence[Optional[float]]) -> str:
    present = [v for v in values if v is not None]
    if not present:
        return ''
    low, high = min(present), max(present)
    span = high - low
    chars = []
    for value in values:
        if value is None:
            chars.append(' ')
        elif span <= 0:
            chars.append(SPARK_CHARS[3])
        else:
            chars.append(SPARK_CHARS[min(int((value - low) / span * len(SPARK_CHARS)), len(SPARK_CHARS) - 1)])
    return ''.join(chars)


# --- Visual specs per alert type ----------------------------------------------------------------
def trade_visual(
    *, side: str, symbol: str, price: Optional[float], pnl: Optional[float], trigger: Optional[str], history: Sequence[Dict[str, Any]]
) -> Dict[str, Any]:
    side = side.upper()
    if side == 'SELL' and pnl is not None:
        visual = {
            'tone': 'positive' if pnl >= 0 else 'negative',
            'headline': f"{'+' if pnl >= 0 else '−'}{_money(abs(pnl))}",
            'headline_label': f'Realized P/L · {trigger} exit' if trigger else 'Realized P/L on this sell',
        }
    else:
        visual = {
            'tone': 'positive' if side == 'BUY' else 'neutral',
            'headline': f"{'▲' if side == 'BUY' else '▼'} {side}" + (f' @ {_money(price)}' if price else ''),
            'headline_label': f'{trigger} exit filled' if trigger else f'{symbol} order filled',
        }
    points = price_points(history)
    if len(points) >= 2:
        chart: Dict[str, Any] = {'kind': 'price', 'title': f'{symbol} · last {len(points)} closes', 'points': points}
        if price:
            chart['marker'] = {'value': float(price), 'label': f'{side} {_money(float(price))}', 'side': side}
        visual['chart'] = chart
    return visual


def news_visual(*, symbol: str, worst_score: float, latest_sentiment: Optional[float], daily: Sequence[Dict[str, Any]], critical: bool, now: Optional[datetime] = None) -> Dict[str, Any]:
    return {
        'tone': 'negative' if critical else 'warning',
        'headline': _signed(worst_score),
        'headline_label': f'Worst headline score · {sentiment_word(worst_score)}',
        'gauge': {
            'kind': 'sentiment',
            'label': 'Overall news sentiment' if latest_sentiment is not None else 'Worst headline',
            'value': latest_sentiment if latest_sentiment is not None else worst_score,
        },
        'chart': {'kind': 'sentiment', 'title': f'{symbol} · daily news sentiment', 'points': sentiment_points(daily, now=now), 'highlight_last': True},
    }


def price_shock_visual(*, symbol: str, move_pct: float, volatility: float, threshold: float, history: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    visual: Dict[str, Any] = {
        'tone': 'negative' if move_pct < 0 else 'warning',
        'headline': f'{_signed(move_pct)}%',
        'headline_label': 'Move vs previous close' + (f' · {abs(move_pct) / volatility:.1f}x typical' if volatility > 0 else ''),
        'gauge': {'kind': 'ratio', 'label': 'Size of the move', 'value': abs(move_pct), 'threshold': threshold, 'typical': volatility or None, 'unit': '%'},
    }
    points = price_points(history)
    if len(points) >= 2:
        visual['chart'] = {'kind': 'price', 'title': f'{symbol} · last {len(points)} closes', 'points': points, 'highlight_last': True}
    return visual


def sentiment_shock_visual(*, symbol: str, previous_avg: float, latest_avg: float, daily: Sequence[Dict[str, Any]], now: Optional[datetime] = None) -> Dict[str, Any]:
    return {
        'tone': 'warning',
        'headline': f'{_signed(previous_avg)} → {_signed(latest_avg)}',
        'headline_label': f'News sentiment, day over day · {sentiment_word(latest_avg)}',
        'gauge': {'kind': 'sentiment', 'label': 'Latest day', 'value': latest_avg},
        'chart': {'kind': 'sentiment', 'title': f'{symbol} · daily news sentiment', 'points': sentiment_points(daily, now=now), 'highlight_last': True},
    }


def test_visual(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Sample visuals so members can preview what real alerts look like."""
    today = (now or datetime.now(timezone.utc)).date()
    closes = [100, 101.2, 100.6, 102.4, 103.1, 102.2, 104.0, 105.3, 104.6, 106.2, 107.0, 106.1, 108.4, 109.2]
    history = [{'date': (today - timedelta(days=len(closes) - 1 - i)).isoformat(), 'close': c} for i, c in enumerate(closes)]
    return {
        'tone': 'positive',
        'headline': 'Sample',
        'headline_label': 'Real alerts show the key number here',
        'gauge': {'kind': 'sentiment', 'label': 'Sample news sentiment', 'value': 0.42},
        'chart': {
            'kind': 'price',
            'title': 'Sample asset · last 14 closes',
            'points': price_points(history),
            'marker': {'value': 104.0, 'label': 'BUY $104.00', 'side': 'BUY'},
        },
    }


# --- PNG chart (email inline image + Discord attachment) ------------------------------------------
def render_chart_png(visual: Optional[Dict[str, Any]]) -> Optional[bytes]:
    """The visual's chart as a PNG, or None (no chart, too little data, or matplotlib missing)."""
    chart = (visual or {}).get('chart')
    if not chart:
        return None
    points = chart.get('points') or []
    if sum(1 for _, v in points if v is not None) < 2:
        return None
    try:
        from matplotlib.figure import Figure  # object API: no global pyplot state, safe in threads
    except Exception:
        return None

    try:
        fig = Figure(figsize=(6.4, 2.6), dpi=160)
        ax = fig.add_axes([0.075, 0.16, 0.9, 0.68])
        labels = [_short_date(label) for label, _ in points]
        values = [v for _, v in points]
        xs = list(range(len(points)))
        tone_color = TONE_COLORS.get(visual.get('tone'), NAVY)

        if chart.get('kind') == 'sentiment':
            bar_x = [x for x, v in zip(xs, values) if v is not None]
            # A day at exactly 0 still gets a thin bar, so it doesn't look like a day without news.
            bar_v = [v if abs(v) >= 0.03 else (0.03 if v >= 0 else -0.03) for v in values if v is not None]
            colors = [GREEN if v >= 0.1 else RED if v <= -0.1 else '#94a3b8' for v in bar_v]
            bars = ax.bar(bar_x, bar_v, width=0.68, color=colors, zorder=3)
            if chart.get('highlight_last') and bar_x and bar_x[-1] == xs[-1]:
                bars[-1].set_edgecolor(NAVY)
                bars[-1].set_linewidth(2)
            for x, v in zip(xs, values):
                if v is None:  # no news that day
                    ax.plot([x], [0], marker='o', markersize=3, color='#cbd5e1', zorder=3)
            ax.axhline(0, color='#94a3b8', linewidth=0.8, zorder=2)
            ax.set_ylim(-1, 1)
            ax.set_yticks([-1, -0.5, 0, 0.5, 1])
            ax.set_yticklabels(['−1', '−0.5', '0', '+0.5', '+1'])
        else:
            # With a highlighted last move, the history is navy so the move itself stands out.
            line_color = NAVY if chart.get('highlight_last') or visual.get('tone') == 'neutral' else tone_color
            ax.plot(xs, values, color=line_color, linewidth=2, zorder=3)
            low = min(values)
            ax.fill_between(xs, values, low, color=line_color, alpha=0.1, zorder=2)
            if chart.get('highlight_last') and len(xs) >= 2:
                ax.plot(xs[-2:], values[-2:], color=RED if values[-1] < values[-2] else AMBER, linewidth=3.2, zorder=4)
            ax.plot([xs[-1]], [values[-1]], marker='o', markersize=6, color=line_color, markeredgecolor='white', zorder=5)
            marker = chart.get('marker')
            if marker and marker.get('value') is not None:
                m_value = float(marker['value'])
                m_color = GREEN if marker.get('side') == 'BUY' else RED
                ax.axhline(m_value, color=m_color, linewidth=1.2, linestyle=(0, (4, 3)), zorder=4)
                ax.plot([xs[-1]], [m_value], marker='^' if marker.get('side') == 'BUY' else 'v', markersize=10, color=m_color, zorder=6)
                ax.annotate(
                    marker.get('label') or '', xy=(0.01, m_value), xycoords=('axes fraction', 'data'),
                    xytext=(0, 4), textcoords='offset points', fontsize=8, fontweight='bold', color=m_color,
                )
                low = min(low, m_value)
                high = max(max(values), m_value)
            else:
                high = max(values)
            pad = (high - low) * 0.12 or abs(high) * 0.01 or 1
            ax.set_ylim(low - pad, high + pad)

        step = max(1, len(xs) // 6)
        ticks = sorted(set(xs[::step]) | {xs[-1]})
        ax.set_xticks(ticks)
        ax.set_xticklabels([labels[i] for i in ticks])
        ax.set_xlim(-0.6, xs[-1] + 0.6)
        for side in ('top', 'right', 'left'):
            ax.spines[side].set_visible(False)
        ax.spines['bottom'].set_color('#cbd5e1')
        ax.tick_params(colors=MUTED, labelsize=8, length=0)
        ax.grid(axis='y', color='#eef2f7', linewidth=0.8, zorder=0)
        fig.text(0.075, 0.9, chart.get('title') or '', fontsize=10, fontweight='bold', color=NAVY)

        buffer = io.BytesIO()
        fig.savefig(buffer, format='png', facecolor='white')
        return buffer.getvalue()
    except Exception as exc:
        logger.warning('notify_chart_render_failed error=%s', exc)
        return None


# --- Email HTML ---------------------------------------------------------------------------------
def _cell_bar(colors: Sequence[str], heights: Sequence[int]) -> str:
    cells = ''.join(
        f'<td style="padding:0 1px"><div style="height:{h}px;background:{c};border-radius:2px;line-height:{h}px;font-size:1px">&nbsp;</div></td>'
        for c, h in zip(colors, heights)
    )
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="table-layout:fixed"><tr valign="middle">{cells}</tr></table>'


def _sentiment_gauge_html(gauge: Dict[str, Any]) -> str:
    value = float(gauge['value'])
    index = gauge_index(value, -1.0, 1.0)
    colors, heights = [], []
    for i in range(GAUGE_CELLS):
        mid = -1 + (i + 0.5) * 2 / GAUGE_CELLS
        base = '#fecaca' if mid <= -0.1 else '#bbf7d0' if mid >= 0.1 else TRACK
        colors.append(NAVY if i == index else base)
        heights.append(18 if i == index else 10)
    color = GREEN if value >= 0.1 else RED if value <= -0.1 else MUTED
    return (
        f'<div style="font-size:12px;color:{MUTED};margin-bottom:6px">{escape(gauge.get("label") or "Sentiment")}: '
        f'<b style="color:{color}">{_signed(value)} {sentiment_word(value)}</b></div>'
        + _cell_bar(colors, heights)
        + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:4px;font-size:11px;color:{MUTED}">'
        '<tr><td align="left">Bearish −1</td><td align="center">Neutral</td><td align="right">+1 Bullish</td></tr></table>'
    )


def _ratio_gauge_html(gauge: Dict[str, Any], tone_color: str) -> str:
    value = abs(float(gauge['value']))
    threshold = float(gauge.get('threshold') or 0)
    high = ratio_scale_max(gauge)
    filled = gauge_index(value, 0, high) + 1
    mark = gauge_index(threshold, 0, high) if threshold else None
    colors = [tone_color if i < filled else TRACK for i in range(GAUGE_CELLS)]
    heights = [18 if i == mark else 12 for i in range(GAUGE_CELLS)]
    if mark is not None:
        colors[mark] = NAVY if mark < filled else '#94a3b8'  # the alert threshold stays visible
    unit = gauge.get('unit') or ''
    typical = gauge.get('typical')
    note = f'alert at {threshold:.1f}{unit}' + (f' · typical day {float(typical):.1f}{unit}' if typical else '')
    return (
        f'<div style="font-size:12px;color:{MUTED};margin-bottom:6px">{escape(gauge.get("label") or "")}: '
        f'<b style="color:{tone_color}">{value:.1f}{unit}</b> · {escape(note)}</div>'
        + _cell_bar(colors, heights)
    )


def _sentiment_days_html(points: Sequence[Point]) -> str:
    """Coloured day squares under the chart: readable even when images are blocked."""
    cells = []
    for label, value in points:
        if value is None:
            color, text = '#f1f5f9', '·'
        else:
            color = GREEN if value >= 0.1 else RED if value <= -0.1 else '#94a3b8'
            text = ''
        cells.append(
            f'<td style="padding:0 1px" title="{escape(label)}"><div style="height:14px;background:{color};border-radius:3px;'
            f'font-size:10px;line-height:14px;text-align:center;color:#94a3b8">{text or "&nbsp;"}</div></td>'
        )
    first, last = _short_date(points[0][0]), _short_date(points[-1][0])
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="table-layout:fixed;margin-top:8px"><tr>{"".join(cells)}</tr></table>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font-size:11px;color:{MUTED};margin-top:3px">'
        f'<tr><td align="left">{escape(first)}</td><td align="center">green = bullish · red = bearish · grey = neutral</td><td align="right">{escape(last)}</td></tr></table>'
    )


def render_email_html(visual: Optional[Dict[str, Any]], *, chart_cid: Optional[str]) -> str:
    if not visual:
        return ''
    tone_color = TONE_COLORS.get(visual.get('tone'), NAVY)
    parts = [
        f'<div style="font-size:30px;line-height:1.1;font-weight:800;color:{tone_color}">{escape(visual.get("headline") or "")}</div>'
        f'<div style="margin-top:4px;font-size:12px;color:{MUTED}">{escape(visual.get("headline_label") or "")}</div>'
    ]
    gauge = visual.get('gauge')
    if gauge:
        body = _sentiment_gauge_html(gauge) if gauge.get('kind') == 'sentiment' else _ratio_gauge_html(gauge, tone_color)
        parts.append(f'<div style="margin-top:16px">{body}</div>')
    chart = visual.get('chart')
    if chart_cid:
        parts.append(
            f'<div style="margin-top:16px"><img src="cid:{chart_cid}" width="512" alt="{escape(chart.get("title") or "Chart")}" '
            'style="display:block;width:100%;max-width:512px;height:auto;border:1px solid rgba(15,23,42,0.08);border-radius:12px"></div>'
        )
    if chart and chart.get('kind') == 'sentiment' and chart.get('points'):
        parts.append(_sentiment_days_html(chart['points']))
    inner = ''.join(parts)
    return (
        '<tr><td style="padding:6px 24px 10px">'
        f'<div style="padding:16px;border-radius:14px;background:#f8fafc;border:1px solid rgba(15,23,42,0.08);border-left:4px solid {tone_color}">{inner}</div>'
        '</td></tr>'
    )


def render_text_lines(visual: Optional[Dict[str, Any]]) -> List[str]:
    """Plain-text email part."""
    if not visual:
        return []
    lines = [f"{visual.get('headline')}  ({visual.get('headline_label')})"]
    gauge = visual.get('gauge')
    if gauge and gauge.get('kind') == 'sentiment':
        index = gauge_index(float(gauge['value']), -1, 1)
        bar = ''.join('|' if i == index else '-' for i in range(GAUGE_CELLS))
        lines.append(f"{gauge.get('label')}: bearish [{bar}] bullish  {_signed(float(gauge['value']))}")
    elif gauge:
        filled = gauge_index(abs(float(gauge['value'])), 0, ratio_scale_max(gauge)) + 1
        lines.append(f"{gauge.get('label')}: [{'#' * filled}{'-' * (GAUGE_CELLS - filled)}] {abs(float(gauge['value'])):.1f}{gauge.get('unit') or ''}")
    chart = visual.get('chart')
    if chart and chart.get('kind') == 'price':
        lines.append(f"{chart.get('title')}: {sparkline([v for _, v in chart['points']])}")
    return lines


# --- Discord ------------------------------------------------------------------------------------
def render_discord_text(visual: Optional[Dict[str, Any]]) -> str:
    """Markdown block for the top of the embed description."""
    if not visual:
        return ''
    lines = [f"## {visual.get('headline')}", f"-# {visual.get('headline_label')}"]
    gauge = visual.get('gauge')
    if gauge and gauge.get('kind') == 'sentiment':
        value = float(gauge['value'])
        index = gauge_index(value, -1, 1)
        bar = ''.join('●' if i == index else '━' for i in range(GAUGE_CELLS))
        lines.append(f"{gauge.get('label')}: **{_signed(value)}** {sentiment_word(value)}")
        lines.append(f"🔴 `{bar}` 🟢")
    elif gauge:
        value = abs(float(gauge['value']))
        high = ratio_scale_max(gauge)
        filled = gauge_index(value, 0, high) + 1
        threshold = float(gauge.get('threshold') or 0)
        mark = gauge_index(threshold, 0, high) if threshold else None
        cells = ['┃' if i == mark else '▰' if i < filled else '▱' for i in range(GAUGE_CELLS)]  # ┃ = alert threshold
        unit = gauge.get('unit') or ''
        typical = gauge.get('typical')
        lines.append(
            f"{gauge.get('label')}: **{value:.1f}{unit}** (alert at {threshold:.1f}{unit}"
            + (f", typical {float(typical):.1f}{unit})" if typical else ')')
        )
        lines.append(f"`{''.join(cells)}`")
    chart = visual.get('chart')
    if chart and chart.get('kind') == 'sentiment' and chart.get('points'):
        squares = ''.join(
            '▫️' if v is None else '🟩' if v >= 0.1 else '🟥' if v <= -0.1 else '⬜' for _, v in chart['points']
        )
        lines.append(f"Last {len(chart['points'])} days: {squares}")
    elif chart and chart.get('kind') == 'price':
        values = [v for _, v in chart['points']]
        lines.append(f"{chart.get('title')}: `{sparkline(values)}`")
    return '\n'.join(lines)
