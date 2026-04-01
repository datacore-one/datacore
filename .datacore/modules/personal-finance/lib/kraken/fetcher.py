"""
Kraken transaction fetcher for Personal Finance module.

Fetches ledger data from Kraken and converts to unified Transaction model.
"""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from pathlib import Path

from .client import KrakenClient
from ..models import Transaction, normalize_asset
from ..storage import (
    load_transactions,
    save_transactions,
    merge_transactions,
    transactions_to_dataframe
)


def fetch_kraken_transactions(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    key_path: Optional[Path] = None
) -> List[Transaction]:
    """
    Fetch transactions from Kraken API.

    Args:
        start: Start date filter
        end: End date filter
        key_path: Path to kraken.key file

    Returns:
        List of Transaction objects
    """
    client = KrakenClient(key_path=key_path)
    ledgers = client.get_ledgers(start=start, end=end)

    transactions = []
    for entry in ledgers:
        tx = _ledger_to_transaction(entry)
        if tx:
            transactions.append(tx)

    return transactions


def _ledger_to_transaction(entry: dict) -> Optional[Transaction]:
    """Convert Kraken ledger entry to Transaction model."""
    try:
        ledger_id = entry.get('id', '')
        refid = entry.get('refid', '')
        timestamp = datetime.fromtimestamp(entry.get('time', 0))
        raw_asset = entry.get('asset', '')
        amount = Decimal(str(entry.get('amount', 0)))
        fee = Decimal(str(entry.get('fee', 0)))
        raw_type = entry.get('type', '')

        # Normalize asset symbol
        asset = normalize_asset(raw_asset, source='kraken')

        # Determine direction
        direction = 'in' if amount >= 0 else 'out'
        amount = abs(amount)

        # Map Kraken types to our types
        type_map = {
            'deposit': 'deposit',
            'withdrawal': 'withdrawal',
            'trade': 'trade',
            'transfer': 'transfer',
            'margin': 'margin',
            'rollover': 'fee',
            'spend': 'trade',
            'receive': 'trade',
        }
        tx_type = type_map.get(raw_type, raw_type)

        return Transaction(
            id=f"kraken_{ledger_id}",
            source='kraken',
            timestamp=timestamp,
            asset=asset,
            amount=amount,
            direction=direction,
            tx_type=tx_type,
            raw_type=raw_type,
            fee_amount=fee if fee > 0 else None,
            fee_asset=asset if fee > 0 else None,
            notes=f"Ref: {refid}" if refid else None,
        )

    except Exception as e:
        print(f"Warning: Could not parse Kraken entry: {e}")
        return None


def sync_kraken_transactions(
    key_path: Optional[Path] = None,
    full_sync: bool = False
) -> int:
    """
    Sync transactions from Kraken to local storage.

    Args:
        key_path: Path to kraken.key file
        full_sync: If True, fetch all history. Otherwise, fetch from last sync.

    Returns:
        Number of new transactions added
    """
    # Load existing transactions
    existing_df = load_transactions('kraken')

    # Determine start date
    start = None
    if not full_sync and not existing_df.empty:
        # Start from last transaction timestamp
        last_ts = existing_df['timestamp'].max()
        start = last_ts.to_pydatetime()

    # Fetch new transactions
    print(f"Fetching Kraken transactions{' since ' + str(start) if start else ''}...")
    new_transactions = fetch_kraken_transactions(start=start, key_path=key_path)

    if not new_transactions:
        print("No new Kraken transactions found.")
        return 0

    # Convert to DataFrame
    new_df = transactions_to_dataframe(new_transactions)

    # Merge and save
    merged_df = merge_transactions(existing_df, new_df)
    save_transactions(merged_df, 'kraken')

    new_count = len(merged_df) - len(existing_df)
    print(f"Added {new_count} new Kraken transactions.")
    return new_count


def get_kraken_withdrawals_to_address(address: str) -> List[Transaction]:
    """
    Get Kraken withdrawals that might have gone to a specific address.

    Note: Kraken ledger entries don't include destination addresses,
    so this returns all withdrawals for manual matching with on-chain data.
    """
    df = load_transactions('kraken')
    if df.empty:
        return []

    # Filter to withdrawals
    withdrawals = df[df['tx_type'] == 'withdrawal']

    from ..storage import dataframe_to_transactions
    return dataframe_to_transactions(withdrawals)
