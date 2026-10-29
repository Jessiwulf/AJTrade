"""Feature #8: Real-time Notification System.

Watches the trading bot's execution log and the AI sentiment pipeline in the background and
alerts each member on the channels they enabled (Email by default, Discord webhook optionally).

- Trade executions: every `Executed` row in bot_execution_logs is claimed exactly once
  (bot_execution_logs.notified_at) and announced as a BUY or SELL alert.
- Market risk: each fresh AI insight is checked for severe negative news catalysts
  (severe bearish news warnings) and market anomalies (abnormal price move, sentiment collapse).

Every alert is de-duplicated per member by an event key (notification_log), so a catalyst or
an order is reported once even though the pipeline re-evaluates it every few minutes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from html import escape
from typing import Any, Dict, List, Optional, Set

import httpx

from app.core import notify_visuals as visuals
from app.core.crypto import decrypt_api_key, encrypt_api_key
from app.core.db import get_database

logger = logging.getLogger('ajtrade.notifier')


def outlook_label(signal: Any) -> str:
    """The AI's direction as a market outlook, never as a buy/sell instruction."""
    return {'BUY': 'Bullish', 'SELL': 'Bearish', 'HOLD': 'Neutral'}.get(str(signal or '').upper(), 'n/a')


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# --- Detection thresholds ----------------------------------------------------------------
# FinBERT scores are P(positive) - P(negative) in [-1, 1]; -0.75 means the model puts at least
# 75 points more probability on "negative" than on "positive" for that headline.
SEVERE_HEADLINE_SCORE = _env_float('AJTRADE_NOTIFY_SEVERE_HEADLINE_SCORE', -0.75)
# Latest-day sentiment at or below this confirms a bearish regime (matches "Strong Bearish").
BEARISH_SENTIMENT_SCORE = _env_float('AJTRADE_NOTIFY_BEARISH_SENTIMENT', -0.35)
# A day-over-day sentiment drop at least this large, landing in bearish territory, is a collapse.
SENTIMENT_SHOCK_DROP = _env_float('AJTRADE_NOTIFY_SENTIMENT_SHOCK_DROP', 0.6)
# Abnormal move: at least this many typical daily moves (std dev) and never less than the floor.
PRICE_SHOCK_SIGMA = _env_float('AJTRADE_NOTIFY_PRICE_SHOCK_SIGMA', 3.0)
PRICE_SHOCK_MIN_PCT = _env_float('AJTRADE_NOTIFY_PRICE_SHOCK_MIN_PCT', 5.0)
# Only headlines/prices this recent are treated as live catalysts.
CATALYST_LOOKBACK_DAYS = 1
# Fills older than this when first seen (e.g. backend was down) are marked done without alerting.
TRADE_ALERT_MAX_AGE = timedelta(hours=72)
TRADE_MONITOR_BATCH = 50

FRONTEND_URL = (os.environ.get('FRONTEND_URL') or 'http://localhost:3000').rstrip('/')

# --- Event catalog -------------------------------------------------------------------------
EVENT_CATALOG: List[Dict[str, Any]] = [
    {
        'key': 'trade_buy',
        'category': 'trading',
        'severity': 'info',
        'label': 'Buy order executed',
        'description': 'The trading bot filled a BUY order on your broker account.',
    },
    {
        'key': 'trade_sell',
        'category': 'trading',
        'severity': 'info',
        'label': 'Sell order executed',
        'description': 'The trading bot filled a SELL order, including stop-loss, trailing-stop and take-profit exits.',
    },
    {
        'key': 'panic_sell',
        'category': 'market',
        'severity': 'critical',
        'label': 'Severe bearish news warning',
        'description': 'A severely negative news catalyst confirmed by a Bearish AI outlook or bearish sentiment.',
    },
    {
        'key': 'negative_news',
        'category': 'market',
        'severity': 'warning',
        'label': 'Severe negative news',
        'description': 'FinBERT scores a fresh headline about your asset as severely negative.',
    },
    {
        'key': 'price_shock',
        'category': 'market',
        'severity': 'warning',
        'label': 'Abnormal price move',
        'description': f'Daily move of at least {PRICE_SHOCK_SIGMA:g}x the asset\'s typical volatility (min {PRICE_SHOCK_MIN_PCT:g}%).',
    },
    {
        'key': 'sentiment_shock',
        'category': 'market',
        'severity': 'warning',
        'label': 'Sentiment collapse',
        'description': 'News sentiment for your asset swings sharply bearish from one day to the next.',
    },
]
EVENT_KEYS = [item['key'] for item in EVENT_CATALOG]
_EVENT_BY_KEY = {item['key']: item for item in EVENT_CATALOG}
DEFAULT_EVENTS = {key: True for key in EVENT_KEYS}

_SEVERITY_COLORS = {'info': 0x16A34A, 'success': 0x16A34A, 'warning': 0xF59E0B, 'critical': 0xDC2626}
_SELL_COLOR = 0x1E3A8A

DISCORD_WEBHOOK_RE = re.compile(
    r'^https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/(\d{5,30})/([A-Za-z0-9._-]{20,200})$'
)

# --- Schema ---------------------------------------------------------------------------------
_schema_ready = False
_schema_lock = asyncio.Lock()


async def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    async with _schema_lock:
        if _schema_ready:
            return
        from app.api.trading_bot import _ensure_schema as ensure_bot_schema

        await ensure_bot_schema()
        db = get_database()
        await db.execute(
            query=(
                "CREATE TABLE IF NOT EXISTS notification_preferences ("
                "owner_id uuid PRIMARY KEY,"
                "email text,"
                "email_enabled boolean NOT NULL DEFAULT true,"
                "discord_enabled boolean NOT NULL DEFAULT false,"
                "discord_webhook_enc bytea,"
                "discord_webhook_hint text,"
                "events jsonb NOT NULL DEFAULT '{}'::jsonb,"
                "updated_at timestamptz NOT NULL DEFAULT now()"
                ")"
            )
        )
        await db.execute(
            query=(
                "CREATE TABLE IF NOT EXISTS notification_log ("
                "id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
                "owner_id uuid NOT NULL,"
                "event_key text NOT NULL,"
                "event_type text NOT NULL,"
                "severity text NOT NULL,"
                "symbol text,"
                "title text NOT NULL,"
                "message text NOT NULL,"
                "channels jsonb NOT NULL DEFAULT '{}'::jsonb,"
                "status text NOT NULL,"
                "created_at timestamptz NOT NULL DEFAULT now(),"
                "UNIQUE (owner_id, event_key)"
                ")"
            )
        )
        await db.execute(
            query="CREATE INDEX IF NOT EXISTS idx_notification_log_owner_created ON notification_log(owner_id, created_at DESC)"
        )

        # The trade monitor marks each execution log row when it has been announced. On first
        # migration, settled history is marked as done so members aren't flooded with old fills;
        # Pending orders stay unmarked so their eventual fill is still announced.
        has_column = await db.fetch_val(
            query=(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'bot_execution_logs' AND column_name = 'notified_at'"
            )
        )
        if not has_column:
            await db.execute(query="ALTER TABLE bot_execution_logs ADD COLUMN IF NOT EXISTS notified_at timestamptz")
            await db.execute(
                query="UPDATE bot_execution_logs SET notified_at = now() WHERE notified_at IS NULL AND action_taken <> 'Pending'"
            )
        await db.execute(
            query=(
                "CREATE INDEX IF NOT EXISTS idx_bot_logs_unnotified ON bot_execution_logs(timestamp) "
                "WHERE action_taken = 'Executed' AND notified_at IS NULL"
            )
        )
        _schema_ready = True


# --- Preferences ----------------------------------------------------------------------------
def _decode_json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or '{}')
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize_events(raw: Any) -> Dict[str, bool]:
    stored = _decode_json(raw)
    return {key: bool(stored.get(key, DEFAULT_EVENTS[key])) for key in EVENT_KEYS}


def mask_webhook(url: str) -> str:
    match = DISCORD_WEBHOOK_RE.match(url or '')
    if not match:
        return 'Discord webhook'
    return f"discord.com/api/webhooks/{match.group(1)[:4]}…/…{match.group(2)[-4:]}"


def validate_discord_webhook(url: str) -> str:
    cleaned = (url or '').strip()
    if not DISCORD_WEBHOOK_RE.match(cleaned):
        raise ValueError(
            'Enter a Discord webhook URL like https://discord.com/api/webhooks/<id>/<token> '
            '(Server Settings > Integrations > Webhooks > Copy Webhook URL).'
        )
    return cleaned


def _webhook_aad(owner_id: str) -> bytes:
    # Binds the ciphertext to its owner so a row copied to another member fails to decrypt.
    return f'discord-webhook:{owner_id}'.encode('utf-8')


async def load_preferences(owner_id: str) -> Dict[str, Any]:
    await ensure_schema()
    row = await get_database().fetch_one(
        query="SELECT * FROM notification_preferences WHERE owner_id = :owner_id",
        values={'owner_id': owner_id},
    )
    if not row:
        return {
            'owner_id': owner_id,
            'email': None,
            'email_enabled': True,
            'discord_enabled': False,
            'discord_webhook_enc': None,
            'discord_webhook_hint': None,
            'events': dict(DEFAULT_EVENTS),
            'updated_at': None,
        }
    prefs = dict(row)
    prefs['events'] = _normalize_events(prefs.get('events'))
    return prefs


async def save_preferences(
    owner_id: str,
    *,
    email: Optional[str] = None,
    email_enabled: Optional[bool] = None,
    discord_enabled: Optional[bool] = None,
    discord_webhook_url: Optional[str] = None,
    remove_discord_webhook: bool = False,
    events: Optional[Dict[str, bool]] = None,
) -> Dict[str, Any]:
    current = await load_preferences(owner_id)

    webhook_enc = current.get('discord_webhook_enc')
    webhook_hint = current.get('discord_webhook_hint')
    if remove_discord_webhook:
        webhook_enc, webhook_hint = None, None
    elif discord_webhook_url:
        url = validate_discord_webhook(discord_webhook_url)
        webhook_enc = encrypt_api_key(url.encode('utf-8'), aad=_webhook_aad(owner_id))
        webhook_hint = mask_webhook(url)

    next_discord = current['discord_enabled'] if discord_enabled is None else bool(discord_enabled)
    if next_discord and not webhook_enc:
        raise ValueError('Add a Discord webhook URL before enabling Discord alerts.')

    merged_events = dict(current['events'])
    for key, value in (events or {}).items():
        if key in _EVENT_BY_KEY:
            merged_events[key] = bool(value)

    await get_database().execute(
        query=(
            "INSERT INTO notification_preferences "
            "(owner_id, email, email_enabled, discord_enabled, discord_webhook_enc, discord_webhook_hint, events, updated_at) "
            "VALUES (:owner_id, :email, :email_enabled, :discord_enabled, :webhook_enc, :webhook_hint, CAST(:events AS jsonb), now()) "
            "ON CONFLICT (owner_id) DO UPDATE SET "
            "email = COALESCE(EXCLUDED.email, notification_preferences.email), "
            "email_enabled = EXCLUDED.email_enabled, discord_enabled = EXCLUDED.discord_enabled, "
            "discord_webhook_enc = EXCLUDED.discord_webhook_enc, discord_webhook_hint = EXCLUDED.discord_webhook_hint, "
            "events = EXCLUDED.events, updated_at = now()"
        ),
        values={
            'owner_id': owner_id,
            'email': (email or '').strip() or current.get('email'),
            'email_enabled': current['email_enabled'] if email_enabled is None else bool(email_enabled),
            'discord_enabled': next_discord,
            'webhook_enc': webhook_enc,
            'webhook_hint': webhook_hint,
            'events': json.dumps(merged_events),
        },
    )
    return await load_preferences(owner_id)


_remembered_emails: Dict[str, str] = {}


async def remember_member_email(owner_id: str, email: Optional[str]) -> None:
    """Keeps the member's account email on file so background alerts can reach them by default."""
    email = (email or '').strip().lower()
    if not owner_id or not email or _remembered_emails.get(owner_id) == email:
        return
    try:
        await ensure_schema()
        await get_database().execute(
            query=(
                "INSERT INTO notification_preferences (owner_id, email, events) "
                "VALUES (:owner_id, :email, CAST(:events AS jsonb)) "
                "ON CONFLICT (owner_id) DO UPDATE SET email = EXCLUDED.email "
                "WHERE notification_preferences.email IS DISTINCT FROM EXCLUDED.email"
            ),
            values={'owner_id': owner_id, 'email': email, 'events': json.dumps(DEFAULT_EVENTS)},
        )
        _remembered_emails[owner_id] = email
    except Exception as exc:
        logger.warning('notify_remember_email_failed owner=%s error=%s', owner_id, exc)


async def _resolve_email(owner_id: str, prefs: Dict[str, Any]) -> Optional[str]:
    if prefs.get('email'):
        return prefs['email']
    # Fallback for members who haven't opened the app since this feature shipped: Supabase keeps
    # the address in auth.users (readable when the backend uses the Postgres role).
    try:
        email = await get_database().fetch_val(
            query="SELECT email FROM auth.users WHERE id = :owner_id", values={'owner_id': owner_id}
        )
    except Exception:
        return None
    if email:
        await remember_member_email(owner_id, email)
    return email or None


def _decrypt_webhook(owner_id: str, prefs: Dict[str, Any]) -> Optional[str]:
    blob = prefs.get('discord_webhook_enc')
    if not blob:
        return None
    try:
        return decrypt_api_key(bytes(blob), aad=_webhook_aad(owner_id)).decode('utf-8')
    except Exception as exc:
        logger.warning('notify_webhook_decrypt_failed owner=%s error=%s', owner_id, exc)
        return None


# --- Channels -------------------------------------------------------------------------------
def smtp_configured() -> bool:
    return bool((os.environ.get('SMTP_HOST') or '').strip() and _smtp_sender())


def _smtp_sender() -> str:
    return (os.environ.get('SMTP_FROM') or os.environ.get('SMTP_USERNAME') or '').strip()


CHART_CID = 'chart@ajtrade'


def _send_email_sync(to_address: str, subject: str, text_body: str, html_body: str, chart_png: Optional[bytes] = None) -> None:
    host = os.environ['SMTP_HOST'].strip()
    port = int(os.environ.get('SMTP_PORT') or 587)
    username = (os.environ.get('SMTP_USERNAME') or '').strip()
    password = os.environ.get('SMTP_PASSWORD') or ''
    if host.lower() == 'smtp.gmail.com':
        # Google shows App Passwords as "abcd efgh ijkl mnop"; the spaces are not part of it.
        password = ''.join(password.split())
    security =(os.environ.get('SMTP_SECURITY') or ('ssl' if port == 465 else 'starttls')).strip().lower()

    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = _smtp_sender()
    message['To'] = to_address
    message.set_content(text_body)
    message.add_alternative(html_body, subtype='html')
    if chart_png:
        # Inline image referenced by <img src="cid:chart@ajtrade"> in the HTML part.
        message.get_payload()[1].add_related(chart_png, maintype='image', subtype='png', cid=f'<{CHART_CID}>', filename='chart.png')

    context = ssl.create_default_context()
    if security == 'ssl':
        with smtplib.SMTP_SSL(host, port, timeout=20, context=context) as client:
            if username:
                client.login(username, password)
            client.send_message(message)
        return
    with smtplib.SMTP(host, port, timeout=20) as client:
        if security == 'starttls':
            client.starttls(context=context)
        if username:
            client.login(username, password)
        client.send_message(message)


async def _send_email(to_address: str, event: Dict[str, Any]) -> Dict[str, Any]:
    if not smtp_configured():
        return {'status': 'not_configured', 'detail': 'SMTP is not configured on the server'}
    subject = f"[AJTrade] {event['title']}"
    chart_png = event.get('_chart_png')
    text_body, html_body = _render_email(event, chart_cid=CHART_CID if chart_png else None)
    for attempt in range(2):
        try:
            await asyncio.to_thread(_send_email_sync, to_address, subject, text_body, html_body, chart_png)
            return {'status': 'sent', 'to': to_address}
        except (smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused) as exc:
            return {'status': 'failed', 'detail': f'{type(exc).__name__}: {exc}'[:300]}
        except Exception as exc:
            if attempt == 1:
                return {'status': 'failed', 'detail': f'{type(exc).__name__}: {exc}'[:300]}
            await asyncio.sleep(2)
    return {'status': 'failed'}


async def _send_discord(webhook_url: str, event: Dict[str, Any]) -> Dict[str, Any]:
    chart_png = event.get('_chart_png')
    embed = _render_discord_embed(event)
    payload: Dict[str, Any] = {
        'username': 'AJTrade Alerts',
        'allowed_mentions': {'parse': []},
        'embeds': [embed],
    }
    if chart_png:
        embed['image'] = {'url': 'attachment://chart.png'}
        payload['attachments'] = [{'id': 0, 'filename': 'chart.png'}]
    async with httpx.AsyncClient(timeout=15.0) as client:
        for attempt in range(2):
            try:
                if chart_png:
                    response = await client.post(
                        webhook_url,
                        params={'wait': 'true'},
                        data={'payload_json': json.dumps(payload)},
                        files={'files[0]': ('chart.png', chart_png, 'image/png')},
                    )
                else:
                    response = await client.post(webhook_url, params={'wait': 'true'}, json=payload)
            except httpx.HTTPError as exc:
                if attempt == 1:
                    return {'status': 'failed', 'detail': f'{type(exc).__name__}: {exc}'[:300]}
                await asyncio.sleep(2)
                continue
            if response.status_code == 429 and attempt == 0:
                try:
                    retry_after = float(response.json().get('retry_after', 1))
                except Exception:
                    retry_after = 1.0
                await asyncio.sleep(min(max(retry_after, 0.5), 5.0))
                continue
            if response.status_code in (401, 403, 404):
                return {'status': 'failed', 'detail': 'Discord rejected the webhook (it may have been deleted). Update it in Notification settings.'}
            if response.is_success:
                return {'status': 'sent'}
            return {'status': 'failed', 'detail': f'Discord HTTP {response.status_code}'}
    return {'status': 'failed'}


# --- Rendering ------------------------------------------------------------------------------
def _render_email(event: Dict[str, Any], *, chart_cid: Optional[str] = None) -> tuple[str, str]:
    severity = event.get('severity', 'info')
    event_type = event.get('event_type')
    if event_type == 'trade_buy':
        accent, badge = '#16a34a', 'TRADE EXECUTED'
    elif event_type == 'trade_sell':
        accent, badge = '#1e3a8a', 'TRADE EXECUTED'
    elif event_type == 'test':
        accent, badge = '#f59e0b', 'TEST'
    else:
        accent = {'critical': '#dc2626', 'warning': '#f59e0b'}.get(severity, '#f59e0b')
        badge = {'critical': 'CRITICAL', 'warning': 'WARNING'}.get(severity, 'ALERT')
    fields = event.get('fields') or []
    link = event.get('link') or f'{FRONTEND_URL}/dashboard'

    visual = event.get('visual')
    text_lines = [event['title'], '', event['message'], '']
    glance = visuals.render_text_lines(visual)
    if glance:
        text_lines += ['At a glance:', *glance, '']
    text_lines += [f"{name}: {value}" for name, value in fields]
    text_lines += ['', f'Open AJTrade: {link}', '', 'You receive this because alerts are enabled in AJTrade > Notifications.',
                   'AJTrade alerts are informational and are not financial advice.']

    def cell(value: Any) -> str:
        text = escape(str(value))
        if str(value).startswith('https://'):
            return f'<a href="{text}" style="color:#1e3a8a;word-break:break-all">{text}</a>'
        return text

    rows = ''.join(
        f'<tr><td style="padding:6px 0;color:#475569;font-size:13px;width:42%">{escape(str(name))}</td>'
        f'<td style="padding:6px 0;color:#0f172a;font-size:13px;font-weight:600">{cell(value)}</td></tr>'
        for name, value in fields
    )
    html = f"""<!doctype html>
<html><body style="margin:0;background:#f8f9fa;font-family:Inter,Segoe UI,Arial,sans-serif">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f8f9fa;padding:24px 12px">
    <tr><td align="center">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border:1px solid rgba(15,23,42,0.1);border-radius:16px;overflow:hidden">
        <tr><td style="height:4px;background:{accent}"></td></tr>
        <tr><td style="padding:22px 24px 8px">
          <div style="font-size:13px;font-weight:800;color:#1e3a8a">AJTrade</div>
          <div style="display:inline-block;margin-top:12px;padding:3px 10px;border-radius:999px;background:{accent}1f;color:{accent};font-size:11px;font-weight:800;letter-spacing:.06em">{badge}</div>
          <h1 style="margin:10px 0 6px;font-size:20px;line-height:1.3;color:#1e3a8a">{escape(event['title'])}</h1>
          <p style="margin:0;color:#475569;font-size:14px;line-height:1.6">{escape(event['message'])}</p>
        </td></tr>
        {visuals.render_email_html(visual, chart_cid=chart_cid)}
        <tr><td style="padding:8px 24px 4px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid rgba(15,23,42,0.08)">{rows}</table></td></tr>
        <tr><td style="padding:16px 24px 24px">
          <a href="{escape(link)}" style="display:inline-block;padding:11px 18px;border-radius:999px;background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#0b1224;font-weight:800;font-size:14px;text-decoration:none">Open AJTrade</a>
        </td></tr>
        <tr><td style="padding:14px 24px;background:#f8f9fa;color:#64748b;font-size:11px;line-height:1.5">
          You receive this because alerts are enabled in AJTrade &gt; Notifications. Alerts are informational and are not financial advice.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""
    return '\n'.join(text_lines), html


def _render_discord_embed(event: Dict[str, Any]) -> Dict[str, Any]:
    color = _SEVERITY_COLORS.get(event.get('severity', 'info'), 0xF59E0B)
    if event.get('event_type') == 'trade_sell':
        color = _SELL_COLOR
    prefix = {'critical': '🚨 ', 'warning': '⚠️ '}.get(event.get('severity'), '')
    embed: Dict[str, Any] = {
        'title': (prefix + event['title'])[:256],
        'description': '\n\n'.join(part for part in (visuals.render_discord_text(event.get('visual')), event['message']) if part)[:4000],
        'color': color,
        'fields': [
            {'name': str(name)[:256], 'value': str(value)[:1024] or '—', 'inline': len(str(value)) <= 28}
            for name, value in (event.get('fields') or [])[:20]
        ],
        'footer': {'text': 'AJTrade Notification System · not financial advice'},
        'timestamp': (event.get('occurred_at') or datetime.now(timezone.utc)).isoformat(),
    }
    if event.get('link'):
        embed['url'] = event['link']
    return embed


# --- Dispatch -------------------------------------------------------------------------------
_background_tasks: Set[asyncio.Task] = set()


def dispatch_in_background(owner_id: str, event: Dict[str, Any]) -> None:
    """Fire-and-forget dispatch so the trading/AI pipeline never waits on SMTP or Discord."""
    task = asyncio.create_task(dispatch(owner_id, event))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def dispatch(owner_id: str, event: Dict[str, Any], *, force: bool = False) -> Dict[str, Any]:
    """Sends `event` to the member's active channels. `force` (test sends) skips the event-type
    toggle and de-duplication but still honours which channels are on."""
    try:
        await ensure_schema()
        prefs = await load_preferences(owner_id)
        event_type = event['event_type']

        if not force and not prefs['events'].get(event_type, False):
            return {'status': 'skipped', 'reason': 'event_disabled'}

        targets: Dict[str, Any] = {}
        if prefs['email_enabled']:
            email = await _resolve_email(owner_id, prefs)
            targets['email'] = email
        if prefs['discord_enabled']:
            targets['discord'] = _decrypt_webhook(owner_id, prefs)
        if not targets:
            return {'status': 'skipped', 'reason': 'no_active_channel'}

        db = get_database()
        log_id = None
        if not force:
            # Claim the event key first: concurrent evaluations of the same catalyst send once.
            log_id = await db.fetch_val(
                query=(
                    "INSERT INTO notification_log (owner_id, event_key, event_type, severity, symbol, title, message, status) "
                    "VALUES (:owner_id, :event_key, :event_type, :severity, :symbol, :title, :message, 'sending') "
                    "ON CONFLICT (owner_id, event_key) DO NOTHING RETURNING id"
                ),
                values={
                    'owner_id': owner_id,
                    'event_key': event['event_key'][:300],
                    'event_type': event_type,
                    'severity': event.get('severity', 'info'),
                    'symbol': event.get('symbol'),
                    'title': event['title'][:300],
                    'message': event['message'][:2000],
                },
            )
            if log_id is None:
                return {'status': 'duplicate'}

        if event.get('visual') and '_chart_png' not in event:
            # One PNG for every channel; rendering is CPU work, so it runs off the event loop.
            event = {**event, '_chart_png': await asyncio.to_thread(visuals.render_chart_png, event['visual'])}

        async def send(channel: str, target: Optional[str]) -> tuple[str, Dict[str, Any]]:
            if not target:
                detail = 'No email address on file' if channel == 'email' else 'Discord webhook missing or unreadable'
                return channel, {'status': 'failed', 'detail': detail}
            if channel == 'email':
                return channel, await _send_email(target, event)
            return channel, await _send_discord(target, event)

        results = dict(await asyncio.gather(*(send(channel, target) for channel, target in targets.items())))
        sent = [c for c, r in results.items() if r.get('status') == 'sent']
        status = 'sent' if len(sent) == len(results) else ('partial' if sent else 'failed')

        if log_id is not None:
            await db.execute(
                query="UPDATE notification_log SET channels = CAST(:channels AS jsonb), status = :status WHERE id = :id",
                values={'id': log_id, 'channels': json.dumps(results), 'status': status},
            )
        log = logger.info if status == 'sent' else logger.warning
        log('notify_dispatch owner=%s type=%s key=%s status=%s channels=%s',
            owner_id, event_type, event.get('event_key'), status, {c: r.get('status') for c, r in results.items()})
        return {'status': status, 'channels': results}
    except Exception as exc:
        logger.warning('notify_dispatch_failed owner=%s event=%s error=%s', owner_id, event.get('event_key'), exc)
        return {'status': 'failed', 'detail': str(exc)}


# --- Trade execution monitor ----------------------------------------------------------------
def _money(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return '—'


def _build_trade_event(row: Dict[str, Any], mode: Optional[str], price_history: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    payload = _decode_json(row.get('signal_received'))
    metadata = payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}
    side = str(payload.get('side') or 'BUY').upper()
    symbol = str(row.get('asset_symbol') or payload.get('asset_symbol') or '').upper()
    price = row.get('execution_price')
    trigger = metadata.get('trigger') if metadata.get('source') == 'bot-risk-exit' else None
    executed_at = row.get('timestamp') or datetime.now(timezone.utc)

    if trigger:
        title = f"{trigger} exit executed: SELL {symbol} @ {_money(price)}"
        message = f"Your {trigger.lower()} rule closed the bot's {symbol} position."
    else:
        title = f"{side} executed: {symbol} @ {_money(price)}"
        reason = metadata.get('strategy_reason') or payload.get('signal_received')
        message = f"The trading bot filled a {side} order for {symbol}." + (f" Reason: {reason}." if reason else '')

    fields = [
        ('Asset', symbol),
        ('Side', side),
        ('Fill price', _money(price)),
        ('Order amount (requested)', _money(payload.get('requested_amount'))),
    ]
    pnl = None
    if side == 'SELL' and payload.get('estimated_pnl') is not None:
        pnl = float(payload['estimated_pnl'])
        fields.append(('Realized P/L', f"{'+' if pnl >= 0 else '−'}{_money(abs(pnl))}"))
    if metadata.get('strategy'):
        fields.append(('Strategy', metadata['strategy']))
    if payload.get('confidence') is not None:
        fields.append(('AI confidence', f"{float(payload['confidence']) * 100:.0f}%"))
    fields.append(('Mode', 'Live' if mode == 'live' else 'Alpaca paper trading'))
    if row.get('broker_order_id'):
        fields.append(('Broker order ID', str(row['broker_order_id'])))
    fields.append(('Executed at', executed_at.strftime('%Y-%m-%d %H:%M:%S UTC') if hasattr(executed_at, 'strftime') else str(executed_at)))

    return {
        'event_type': 'trade_sell' if side == 'SELL' else 'trade_buy',
        'event_key': f"trade:{row['id']}",
        'severity': 'info',
        'symbol': symbol,
        'title': title,
        'message': message,
        'fields': fields,
        'link': f'{FRONTEND_URL}/automated',
        'occurred_at': executed_at if isinstance(executed_at, datetime) else None,
        'visual': visuals.trade_visual(
            side=side, symbol=symbol, price=_float_or_none(price), pnl=pnl, trigger=trigger, history=price_history or []
        ),
    }


def _float_or_none(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number > 0 else None


async def _latest_price_history(db, owner_id: str, symbol: str) -> List[Dict[str, Any]]:
    """Recent closes saved with the asset's latest AI insight (for the trade alert chart)."""
    try:
        raw = await db.fetch_val(
            query="SELECT payload FROM insights WHERE owner = :owner AND symbol = :symbol ORDER BY updated_at DESC LIMIT 1",
            values={'owner': owner_id, 'symbol': symbol},
        )
    except Exception:
        return []
    history = _decode_json(raw).get('price_history')
    return history if isinstance(history, list) else []


async def process_trade_executions() -> int:
    """Claims newly executed bot orders and alerts their owners. Returns how many were claimed."""
    await ensure_schema()
    db = get_database()
    rows = await db.fetch_all(
        query=(
            "UPDATE bot_execution_logs SET notified_at = now() WHERE id IN ("
            "SELECT id FROM bot_execution_logs WHERE action_taken = 'Executed' AND notified_at IS NULL "
            f"ORDER BY timestamp ASC LIMIT {TRADE_MONITOR_BATCH} FOR UPDATE SKIP LOCKED"
            ") RETURNING id, owner_id, timestamp, asset_symbol, signal_received, execution_price, broker_order_id"
        )
    )
    if not rows:
        return 0

    now = datetime.now(timezone.utc)
    sends = []
    for row in rows:
        row = dict(row)
        owner_id = str(row['owner_id'])
        timestamp = row.get('timestamp')
        if isinstance(timestamp, datetime) and now - timestamp > TRADE_ALERT_MAX_AGE:
            logger.info('notify_trade_skipped_stale log_id=%s owner=%s', row['id'], owner_id)
            continue
        try:
            mode = await db.fetch_val(
                query="SELECT mode FROM trading_rules WHERE owner_id = :owner_id AND asset_symbol = :symbol",
                values={'owner_id': owner_id, 'symbol': row['asset_symbol']},
            )
        except Exception:
            mode = None
        history = await _latest_price_history(db, owner_id, str(row.get('asset_symbol') or '').upper())
        sends.append(dispatch(owner_id, _build_trade_event(row, mode, history)))
    if sends:
        await asyncio.gather(*sends)
    return len(rows)


# --- Market risk monitor (AI sentiment pipeline) --------------------------------------------
def _headline_id(headline: Dict[str, Any]) -> str:
    basis = str(headline.get('url') or headline.get('title') or '')
    return hashlib.sha1(basis.encode('utf-8')).hexdigest()[:16]


def _recent_dates(today: datetime) -> Set[str]:
    return {(today - timedelta(days=offset)).date().isoformat() for offset in range(CATALYST_LOOKBACK_DAYS + 1)}


def detect_market_events(symbol: str, insight: Dict[str, Any], *, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Pure detection over one AI insight; returns alert events (not yet filtered by preferences)."""
    now = now or datetime.now(timezone.utc)
    symbol = str(symbol or insight.get('symbol') or '').upper()
    recent = _recent_dates(now)
    events: List[Dict[str, Any]] = []
    link = f'{FRONTEND_URL}/insights'

    daily = [d for d in (insight.get('news_sentiment_daily') or []) if isinstance(d, dict) and d.get('date')]
    daily.sort(key=lambda d: str(d['date']))
    signal = str(insight.get('signal') or '').upper()
    try:
        confidence = int(insight.get('confidence') or 0)
    except (TypeError, ValueError):
        confidence = 0
    try:
        latest_sentiment = float(insight.get('latest_sentiment_score'))
    except (TypeError, ValueError):
        latest_sentiment = None
    price = insight.get('latest_price')

    # 1) Severe negative news catalyst -> severe bearish news warning (confirmed) or negative-news warning.
    severe = []
    for day in daily:
        if str(day['date'])[:10] not in recent:
            continue
        for headline in day.get('headlines') or []:
            try:
                score = float(headline.get('score'))
            except (TypeError, ValueError):
                continue
            if score <= SEVERE_HEADLINE_SCORE:
                severe.append({**headline, 'score': score, 'date': str(day['date'])[:10]})
    if severe:
        severe.sort(key=lambda h: h['score'])
        worst = severe[0]
        bearish_signal = signal == 'SELL'
        bearish_sentiment = latest_sentiment is not None and latest_sentiment <= BEARISH_SENTIMENT_SCORE
        fields = [
            ('Asset', symbol),
            ('Headline', worst.get('title') or '—'),
            ('Source', worst.get('source') or '—'),
            ('FinBERT score', f"{worst['score']:+.2f} (−1 to +1)"),
            ('Severe headlines (48h)', str(len(severe))),
            ('AI outlook', f"{outlook_label(signal)} ({confidence}% confidence)"),
            ('Latest sentiment', f"{latest_sentiment:+.2f}" if latest_sentiment is not None else 'n/a'),
        ]
        if price:
            fields.append(('Last price', _money(price)))
        if worst.get('url'):
            fields.append(('Article', worst['url']))
        news_chart = visuals.news_visual(
            symbol=symbol, worst_score=worst['score'], latest_sentiment=latest_sentiment, daily=daily,
            critical=bool(bearish_signal or bearish_sentiment), now=now,
        )
        if bearish_signal or bearish_sentiment:
            confirmation = 'the AI outlook is Bearish' if bearish_signal else 'overall news sentiment is strongly bearish'
            events.append({
                'event_type': 'panic_sell',
                'event_key': f"panic_sell:{symbol}:{_headline_id(worst)}",
                'severity': 'critical',
                'symbol': symbol,
                'title': f"Severe bearish news: {symbol}",
                'message': (
                    f"FinBERT flagged a severely negative catalyst for {symbol} and {confirmation}. "
                    "Details below."
                ),
                'fields': fields,
                'link': link,
                'visual': news_chart,
            })
        else:
            events.append({
                'event_type': 'negative_news',
                'event_key': f"negative_news:{symbol}:{_headline_id(worst)}",
                'severity': 'warning',
                'symbol': symbol,
                'title': f"Severe negative news: {symbol}",
                'message': f"FinBERT scored a fresh headline about {symbol} as severely negative. The AI outlook has not turned Bearish yet.",
                'fields': fields,
                'link': link,
                'visual': news_chart,
            })

    # 2) Abnormal price move: today's change vs the asset's own typical daily volatility.
    history = [p for p in (insight.get('price_history') or []) if isinstance(p, dict) and p.get('close')]
    if len(history) >= 2:
        last, prev = history[-1], history[-2]
        try:
            move_pct = (float(last['close']) / float(prev['close']) - 1.0) * 100.0
        except (TypeError, ValueError, ZeroDivisionError):
            move_pct = 0.0
        try:
            volatility = float(insight.get('volatility_pct') or 0.0)
        except (TypeError, ValueError):
            volatility = 0.0
        threshold = max(PRICE_SHOCK_MIN_PCT, PRICE_SHOCK_SIGMA * volatility)
        last_date = str(last.get('date') or '')[:10]
        if abs(move_pct) >= threshold and last_date in recent:
            direction = 'down' if move_pct < 0 else 'up'
            sigma = abs(move_pct) / volatility if volatility > 0 else None
            events.append({
                'event_type': 'price_shock',
                'event_key': f"price_shock:{symbol}:{last_date}:{direction}",
                'severity': 'critical' if direction == 'down' else 'warning',
                'symbol': symbol,
                'title': f"Abnormal price move: {symbol} {move_pct:+.2f}%",
                'message': (
                    f"{symbol} moved {move_pct:+.2f}% from the previous close"
                    + (f", about {sigma:.1f}x its typical daily move." if sigma else '.')
                ),
                'fields': [
                    ('Asset', symbol),
                    ('Move', f"{move_pct:+.2f}%"),
                    ('Previous close', f"{float(prev['close']):,.2f} ({prev.get('date')})"),
                    ('Latest close', f"{float(last['close']):,.2f} ({last_date})"),
                    ('Typical daily move', f"{volatility:.2f}%" if volatility else 'n/a'),
                    ('Alert threshold', f"{threshold:.2f}%"),
                ],
                'link': f'{FRONTEND_URL}/dashboard',
                'visual': visuals.price_shock_visual(
                    symbol=symbol, move_pct=move_pct, volatility=volatility, threshold=threshold, history=history
                ),
            })

    # 3) Sentiment collapse: latest news day swings sharply bearish versus the day before.
    if len(daily) >= 2:
        latest_day, previous_day = daily[-1], daily[-2]
        try:
            latest_avg = float(latest_day.get('avg'))
            previous_avg = float(previous_day.get('avg'))
            latest_count = int(latest_day.get('count') or 0)
        except (TypeError, ValueError):
            latest_avg = previous_avg = 0.0
            latest_count = 0
        latest_date = str(latest_day['date'])[:10]
        drop = previous_avg - latest_avg
        if (
            latest_date in recent
            and latest_count >= 2
            and latest_avg <= BEARISH_SENTIMENT_SCORE
            and drop >= SENTIMENT_SHOCK_DROP
        ):
            events.append({
                'event_type': 'sentiment_shock',
                'event_key': f"sentiment_shock:{symbol}:{latest_date}",
                'severity': 'warning',
                'symbol': symbol,
                'title': f"Sentiment collapse: {symbol}",
                'message': (
                    f"Average news sentiment for {symbol} fell from {previous_avg:+.2f} to {latest_avg:+.2f} "
                    f"across {latest_count} articles."
                ),
                'fields': [
                    ('Asset', symbol),
                    (f"Sentiment {previous_day['date']}", f"{previous_avg:+.2f}"),
                    (f"Sentiment {latest_date}", f"{latest_avg:+.2f} ({latest_count} articles)"),
                    ('Drop', f"{drop:.2f}"),
                    ('AI outlook', f"{outlook_label(signal)} ({confidence}% confidence)"),
                ],
                'link': link,
                'visual': visuals.sentiment_shock_visual(
                    symbol=symbol, previous_avg=previous_avg, latest_avg=latest_avg, daily=daily, now=now
                ),
            })

    return events


async def evaluate_market_events(owner_id: str, symbol: str, insight: Dict[str, Any]) -> None:
    """Insight hook for the background pipeline: queues an alert for every detected risk event."""
    try:
        events = detect_market_events(symbol, insight)
        if events:
            await _with_saved_news_history(symbol, insight, events)
        for event in events:
            dispatch_in_background(owner_id, event)
    except Exception as exc:
        logger.warning('notify_market_eval_failed owner=%s symbol=%s error=%s', owner_id, symbol, exc)


async def _with_saved_news_history(symbol: str, insight: Dict[str, Any], events: List[Dict[str, Any]]) -> None:
    """Fills the sentiment charts with the saved 30-day Alpaca news history (the insight alone
    often only covers the last day or two)."""
    if not any(((e.get('visual') or {}).get('chart') or {}).get('kind') == 'sentiment' for e in events):
        return
    try:
        from app.core import news_history

        saved = (await news_history.daily_history([symbol])).get(str(symbol).upper()) or []
    except Exception:
        return
    if not saved:
        return
    daily = news_history.merge_daily(insight.get('news_sentiment_daily') or [], saved)
    for event in events:
        chart = (event.get('visual') or {}).get('chart')
        if chart and chart.get('kind') == 'sentiment':
            chart['points'] = visuals.sentiment_points(daily)


# --- History / test -------------------------------------------------------------------------
async def recent_history(owner_id: str, limit: int = 30) -> List[Dict[str, Any]]:
    await ensure_schema()
    rows = await get_database().fetch_all(
        query=(
            "SELECT id, event_type, severity, symbol, title, message, channels, status, created_at "
            "FROM notification_log WHERE owner_id = :owner_id ORDER BY created_at DESC LIMIT :limit"
        ),
        values={'owner_id': owner_id, 'limit': max(1, min(int(limit), 100))},
    )
    return [
        {
            'id': str(row['id']),
            'event_type': row['event_type'],
            'severity': row['severity'],
            'symbol': row['symbol'],
            'title': row['title'],
            'message': row['message'],
            'channels': _decode_json(row['channels']),
            'status': row['status'],
            'created_at': row['created_at'].isoformat() if row['created_at'] else None,
        }
        for row in rows
    ]




def build_test_event() -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        'event_type': 'test',
        'event_key': f"test:{now.timestamp()}",
        'severity': 'info',
        'symbol': None,
        'title': 'Test alert: your AJTrade notifications work',
        'message': 'This is a test from AJTrade. Trade executions and market risk alerts will arrive on this channel.',
        'fields': [('Sent at', now.strftime('%Y-%m-%d %H:%M:%S UTC'))],
        'link': f'{FRONTEND_URL}/notifications',
        'occurred_at': now,
        'visual': visuals.test_visual(now),
    }
