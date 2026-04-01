"""
Gate.io adapter for Personal Finance module.

Provides access to Gate.io wallet data by leveraging the trading module's
credentials and adding spot wallet functionality.
"""

import os
import gate_api
from pathlib import Path
from datetime import datetime
from decimal import Decimal
from typing import List, Dict, Optional
from dotenv import load_dotenv

from ..models import Transaction
from ..storage import (
    load_transactions,
    save_transactions,
    merge_transactions,
    transactions_to_dataframe
)

# Load trading module's .env
TRADING_GATEIO_DIR = Path.home() / 'Data' / '.datacore' / 'modules' / 'trading' / 'lib' / 'gateio'
load_dotenv(TRADING_GATEIO_DIR / '.env')


def get_spot_api():
    """Get configured Gate.io Spot API client."""
    config = gate_api.Configuration(
        key=os.getenv('GATE_API_KEY'),
        secret=os.getenv('GATE_API_SECRET')
    )
    client = gate_api.ApiClient(config)
    return gate_api.SpotApi(client)


def get_wallet_api():
    """Get configured Gate.io Wallet API client."""
    config = gate_api.Configuration(
        key=os.getenv('GATE_API_KEY'),
        secret=os.getenv('GATE_API_SECRET')
    )
    client = gate_api.ApiClient(config)
    return gate_api.WalletApi(client)


def get_spot_balance() -> Dict[str, Decimal]:
    """Get current spot wallet balance."""
    api = get_spot_api()
    try:
        accounts = api.list_spot_accounts()
        balance = {}
        for account in accounts:
            if float(account.available) > 0 or float(account.locked) > 0:
                total = Decimal(account.available) + Decimal(account.locked)
                balance[account.currency] = total
        return balance
    except Exception as e:
        print(f"Error fetching Gate.io spot balance: {e}")
        return {}


def get_futures_balance() -> Dict[str, Decimal]:
    """Get current futures account balance (from trading module pattern)."""
    config = gate_api.Configuration(
        key=os.getenv('GATE_API_KEY'),
        secret=os.getenv('GATE_API_SECRET')
    )
    client = gate_api.ApiClient(config)
    futures_api = gate_api.FuturesApi(client)

    try:
        accounts = futures_api.list_futures_accounts(settle='usdt')
        balance = {
            'USDT': Decimal(str(accounts.total)),
        }
        return balance
    except Exception as e:
        print(f"Error fetching Gate.io futures balance: {e}")
        return {}


def get_total_balance() -> Dict[str, Decimal]:
    """Get combined spot and futures balance."""
    spot = get_spot_balance()
    futures = get_futures_balance()

    # Merge balances
    combined = spot.copy()
    for currency, amount in futures.items():
        if currency in combined:
            combined[currency] += amount
        else:
            combined[currency] = amount

    return combined


def fetch_deposit_withdrawals(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None
) -> List[Transaction]:
    """
    Fetch deposit and withdrawal history.

    Gate.io API limits to 30 days per query, so we fetch in chunks.
    Default: fetch from 2023-01-01 to now.
    """
    from datetime import timedelta

    transactions = []

    # Default date range: 2 years back
    if end is None:
        end = datetime.now()
    if start is None:
        start = datetime(2023, 1, 1)

    try:
        wallet_api = get_wallet_api()

        # Fetch in 29-day chunks (API max is 30 days)
        current_end = end
        while current_end > start:
            current_start = current_end - timedelta(days=29)
            if current_start < start:
                current_start = start

            from_ts = int(current_start.timestamp())
            to_ts = int(current_end.timestamp())

            # Fetch withdrawals for this period
            try:
                withdrawals = wallet_api.list_withdrawals(
                    _from=from_ts, to=to_ts, limit=500
                )
                for w in withdrawals:
                    tx = Transaction(
                        id=f"gateio_w_{w.id}",
                        source='gateio',
                        timestamp=datetime.fromtimestamp(int(w.timestamp)),
                        asset=w.currency,
                        amount=Decimal(str(w.amount)),
                        direction='out',
                        tx_type='withdrawal',
                        raw_type='withdrawal',
                        to_address=w.address if hasattr(w, 'address') and w.address else None,
                        tx_hash=w.txid if hasattr(w, 'txid') and w.txid else None,
                        fee_amount=Decimal(str(w.fee)) if hasattr(w, 'fee') and w.fee else None,
                        fee_asset=w.currency,
                    )
                    transactions.append(tx)
            except Exception as e:
                print(f"Withdrawals error ({current_start.date()}): {e}")

            # Fetch deposits for this period
            try:
                deposits = wallet_api.list_deposits(
                    _from=from_ts, to=to_ts, limit=500
                )
                for d in deposits:
                    tx = Transaction(
                        id=f"gateio_d_{d.id}",
                        source='gateio',
                        timestamp=datetime.fromtimestamp(int(d.timestamp)),
                        asset=d.currency,
                        amount=Decimal(str(d.amount)),
                        direction='in',
                        tx_type='deposit',
                        raw_type='deposit',
                        from_address=d.address if hasattr(d, 'address') and d.address else None,
                        tx_hash=d.txid if hasattr(d, 'txid') and d.txid else None,
                    )
                    transactions.append(tx)
            except Exception as e:
                print(f"Deposits error ({current_start.date()}): {e}")

            current_end = current_start - timedelta(days=1)

    except Exception as e:
        print(f"Gate.io wallet API error: {e}")

    return transactions


def sync_gateio_transactions() -> int:
    """
    Sync transactions from Gate.io to local storage.

    Returns:
        Number of new transactions added
    """
    # Load existing transactions
    existing_df = load_transactions('gateio')

    # Fetch new transactions
    print("Fetching Gate.io transactions...")
    new_transactions = fetch_deposit_withdrawals()

    if not new_transactions:
        print("No Gate.io transactions found.")
        return 0

    # Convert to DataFrame
    new_df = transactions_to_dataframe(new_transactions)

    # Merge and save
    merged_df = merge_transactions(existing_df, new_df)
    save_transactions(merged_df, 'gateio')

    new_count = len(merged_df) - len(existing_df)
    print(f"Added {new_count} new Gate.io transactions.")
    return new_count
