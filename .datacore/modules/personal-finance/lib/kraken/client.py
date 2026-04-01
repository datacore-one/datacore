"""
Kraken API client for Personal Finance module.

Adapts the existing MoneyMaker class pattern from df-kraken.
"""

import krakenex
from pathlib import Path
from datetime import datetime
from time import sleep
from typing import Optional, List, Dict, Any


# Path to existing kraken.key file
DF_KRAKEN_DIR = Path.home() / 'Data' / '0-personal' / 'code' / 'trading' / 'df-kraken'
DEFAULT_KEY_PATH = DF_KRAKEN_DIR / 'kraken.key'


class KrakenClient:
    """Kraken API client for fetching account data."""

    def __init__(self, key_path: Optional[Path] = None):
        """
        Initialize Kraken client.

        Args:
            key_path: Path to kraken.key file. Defaults to df-kraken location.
        """
        self.key_path = key_path or DEFAULT_KEY_PATH
        self.api = krakenex.API()

        if self.key_path.exists():
            self.api.load_key(str(self.key_path))
        else:
            raise FileNotFoundError(f"Kraken key file not found: {self.key_path}")

    def get_balance(self) -> Dict[str, float]:
        """Get current account balance."""
        response = self.api.query_private('Balance')

        if response.get('error'):
            raise Exception(f"Kraken API error: {response['error']}")

        balance = {}
        for currency, value in response.get('result', {}).items():
            balance[currency] = float(value)
        return balance

    def get_ledgers(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        asset: Optional[str] = None,
        ledger_type: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Fetch ledger entries with pagination.

        Args:
            start: Start time filter
            end: End time filter
            asset: Filter by asset (e.g., 'XETH', 'XXBT')
            ledger_type: Filter by type (deposit, withdrawal, trade, etc.)
            limit: Max entries per request (max 50)

        Returns:
            List of ledger entries
        """
        params = {}

        if start:
            params['start'] = int(start.timestamp())
        if end:
            params['end'] = int(end.timestamp())
        if asset:
            params['asset'] = asset
        if ledger_type:
            params['type'] = ledger_type

        # Fetch with pagination
        all_entries = []
        current_end = int(datetime.now().timestamp()) if not end else int(end.timestamp())
        last_id = ""

        while True:
            params['end'] = current_end

            try:
                response = self.api.query_private('Ledgers', params)

                if response.get('error'):
                    if 'EGeneral:Too many requests' in str(response['error']):
                        sleep(2)
                        continue
                    raise Exception(f"Kraken API error: {response['error']}")

                ledgers = response.get('result', {}).get('ledger', {})

                if not ledgers:
                    break

                for ledger_id, entry in ledgers.items():
                    if ledger_id != last_id:
                        entry['id'] = ledger_id
                        all_entries.append(entry)

                # Get earliest timestamp for next page
                if all_entries:
                    last_entry = list(ledgers.values())[-1]
                    last_id = list(ledgers.keys())[-1]

                    # Check if we've reached the start
                    if current_end == int(last_entry['time']):
                        break

                    current_end = int(last_entry['time'])

                    # Respect rate limits
                    sleep(1)
                else:
                    break

            except Exception as e:
                print(f"Error fetching ledgers: {e}")
                break

        return all_entries

    def get_all_ledgers(self) -> List[Dict[str, Any]]:
        """Fetch all ledger entries (convenience method)."""
        return self.get_ledgers()

    def get_deposits_withdrawals(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch only deposit and withdrawal ledger entries.

        These are the entries needed for loan tracking.
        """
        deposits = self.get_ledgers(start=start, end=end, ledger_type='deposit')
        withdrawals = self.get_ledgers(start=start, end=end, ledger_type='withdrawal')

        all_entries = deposits + withdrawals
        # Sort by time descending
        all_entries.sort(key=lambda x: x.get('time', 0), reverse=True)
        return all_entries

    def get_trades(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """Fetch trade history."""
        params = {}

        if start:
            params['start'] = int(start.timestamp())
        if end:
            params['end'] = int(end.timestamp())

        response = self.api.query_private('TradesHistory', params)

        if response.get('error'):
            raise Exception(f"Kraken API error: {response['error']}")

        trades = response.get('result', {}).get('trades', {})
        return list(trades.values())
