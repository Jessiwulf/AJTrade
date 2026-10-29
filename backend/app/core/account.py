"""Deletes a member's account and every row AJTrade stores about them (PDPA right to erasure).

The member's broker accounts (Alpaca, Settrade) are not touched: AJTrade only stops using them.
Shared public data (news history, market sentiment) is not personal and stays.
"""
import logging
from typing import Dict, List, Tuple

logger = logging.getLogger('ajtrade.account')

# (table, owner column). Portfolios cascade to portfolio_positions, trading_history and
# performance_metrics. Tables created at runtime may not exist yet; those are skipped.
MEMBER_TABLES: List[Tuple[str, str]] = [
    ('portfolios', 'owner'),
    ('watchlists', 'owner'),
    ('encrypted_api_keys', 'owner'),
    ('insights', 'owner'),
    ('news_cache', 'owner'),
    ('alert_events', 'owner_id'),
    ('bot_active_positions', 'owner_id'),
    ('bot_execution_logs', 'owner_id'),
    ('broker_equity_snapshots', 'owner_id'),
    ('finbert_telemetry_events', 'owner_id'),
    ('forecaster_telemetry_events', 'owner_id'),
    ('llm_telemetry_events', 'owner_id'),
    ('notification_log', 'owner_id'),
    ('notification_preferences', 'owner_id'),
    ('trading_rules', 'owner_id'),
    ('user_settings', 'owner_id'),
    ('profiles', 'id'),
]


async def delete_member(db, owner: str) -> Dict[str, int]:
    """Deletes everything for this member, then their login (auth.users), in one transaction:
    either all of it goes or nothing does. Returns rows deleted per table."""
    deleted: Dict[str, int] = {}
    async with db.transaction():
        for table, column in MEMBER_TABLES:
            if not await db.fetch_val(query="SELECT to_regclass(:name) IS NOT NULL", values={'name': f'public.{table}'}):
                continue
            rows = await db.fetch_all(
                query=f"DELETE FROM {table} WHERE {column} = :owner RETURNING 1",  # names are from the list above
                values={'owner': owner},
            )
            deleted[table] = len(rows)
        # The Supabase login itself, so the email can no longer sign in (its sessions go with it).
        rows = await db.fetch_all(query="DELETE FROM auth.users WHERE id = :owner RETURNING 1", values={'owner': owner})
        deleted['auth.users'] = len(rows)
    logger.warning('account_deleted owner=%s rows=%d', owner, sum(deleted.values()))
    return deleted
