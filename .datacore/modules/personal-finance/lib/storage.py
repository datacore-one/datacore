"""
Parquet storage utilities for Personal Finance module.

Handles persistent storage of transactions, loans, and balance snapshots.
Follows patterns from trading module's data storage.
"""

import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import Optional, List
from decimal import Decimal

from .models import Transaction, Loan, BalanceSnapshot

# Module data directory
MODULE_DIR = Path(__file__).parent.parent
DATA_DIR = MODULE_DIR / 'data'
TRANSACTIONS_DIR = DATA_DIR / 'transactions'
LOANS_DIR = DATA_DIR / 'loans'

# Ensure directories exist
TRANSACTIONS_DIR.mkdir(parents=True, exist_ok=True)
LOANS_DIR.mkdir(parents=True, exist_ok=True)


def get_transactions_path(source: str) -> Path:
    """Get path to transactions parquet file for a source."""
    return TRANSACTIONS_DIR / f"{source}_transactions.parquet"


def get_loans_path() -> Path:
    """Get path to loans parquet file."""
    return LOANS_DIR / "loans.parquet"


def load_transactions(source: str) -> pd.DataFrame:
    """Load transactions from parquet file."""
    path = get_transactions_path(source)
    if path.exists():
        try:
            df = pd.read_parquet(path)
            if 'timestamp' in df.columns:
                df['timestamp'] = pd.to_datetime(df['timestamp'])
            return df
        except Exception as e:
            print(f"Warning: Could not load {path}: {e}")
    return pd.DataFrame()


def save_transactions(df: pd.DataFrame, source: str) -> None:
    """Save transactions to parquet file."""
    if df.empty:
        return
    path = get_transactions_path(source)
    try:
        df.to_parquet(path, index=False)
    except Exception as e:
        print(f"Warning: Could not save {path}: {e}")


def merge_transactions(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Merge existing and new transactions, removing duplicates by ID."""
    if existing.empty:
        return new
    if new.empty:
        return existing

    combined = pd.concat([existing, new], ignore_index=True)
    # Remove duplicates based on transaction ID
    combined = combined.drop_duplicates(subset=['id'], keep='last')
    # Sort by timestamp
    combined = combined.sort_values('timestamp', ascending=False)
    return combined


def transactions_to_dataframe(transactions: List[Transaction]) -> pd.DataFrame:
    """Convert list of Transaction models to DataFrame."""
    if not transactions:
        return pd.DataFrame()

    records = []
    for tx in transactions:
        record = tx.model_dump()
        # Convert Decimal to float for parquet compatibility
        for key in ['amount', 'usd_value', 'usd_price', 'fee_amount']:
            if record.get(key) is not None:
                record[key] = float(record[key])
        records.append(record)

    return pd.DataFrame(records)


def dataframe_to_transactions(df: pd.DataFrame) -> List[Transaction]:
    """Convert DataFrame to list of Transaction models."""
    if df.empty:
        return []

    transactions = []
    for _, row in df.iterrows():
        record = row.to_dict()
        # Convert float back to Decimal
        for key in ['amount', 'usd_value', 'usd_price', 'fee_amount']:
            if record.get(key) is not None and pd.notna(record[key]):
                record[key] = Decimal(str(record[key]))
        # Handle NaN values
        for key, value in record.items():
            if pd.isna(value):
                record[key] = None
        transactions.append(Transaction(**record))
    return transactions


def load_loans() -> pd.DataFrame:
    """Load loans from parquet file."""
    path = get_loans_path()
    if path.exists():
        try:
            df = pd.read_parquet(path)
            if 'date_issued' in df.columns:
                df['date_issued'] = pd.to_datetime(df['date_issued'])
            if 'date_due' in df.columns:
                df['date_due'] = pd.to_datetime(df['date_due'])
            return df
        except Exception as e:
            print(f"Warning: Could not load {path}: {e}")
    return pd.DataFrame()


def save_loans(df: pd.DataFrame) -> None:
    """Save loans to parquet file."""
    if df.empty:
        return
    path = get_loans_path()
    try:
        df.to_parquet(path, index=False)
    except Exception as e:
        print(f"Warning: Could not save {path}: {e}")


def loans_to_dataframe(loans: List[Loan]) -> pd.DataFrame:
    """Convert list of Loan models to DataFrame."""
    if not loans:
        return pd.DataFrame()

    records = []
    for loan in loans:
        record = loan.model_dump()
        # Convert Decimal to float for parquet compatibility
        for key in ['principal_amount', 'principal_usd', 'amount_repaid']:
            if record.get(key) is not None:
                record[key] = float(record[key])
        records.append(record)

    return pd.DataFrame(records)


def dataframe_to_loans(df: pd.DataFrame) -> List[Loan]:
    """Convert DataFrame to list of Loan models."""
    if df.empty:
        return []

    loans = []
    for _, row in df.iterrows():
        record = row.to_dict()
        # Convert float back to Decimal
        for key in ['principal_amount', 'principal_usd', 'amount_repaid']:
            if record.get(key) is not None and pd.notna(record[key]):
                record[key] = Decimal(str(record[key]))
        # Handle NaN values
        for key, value in record.items():
            if pd.isna(value):
                record[key] = None
        loans.append(Loan(**record))
    return loans


def get_all_transactions() -> pd.DataFrame:
    """Load and merge transactions from all sources."""
    all_dfs = []
    for source in ['kraken', 'gateio', 'ethereum']:
        df = load_transactions(source)
        if not df.empty:
            all_dfs.append(df)

    if not all_dfs:
        return pd.DataFrame()

    combined = pd.concat(all_dfs, ignore_index=True)
    combined = combined.sort_values('timestamp', ascending=False)
    return combined


def get_transactions_by_address(
    address: str,
    direction: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> pd.DataFrame:
    """Filter transactions by address."""
    df = get_all_transactions()
    if df.empty:
        return df

    address = address.lower()

    # Filter by address (either from or to)
    mask = (
        (df['from_address'].str.lower() == address) |
        (df['to_address'].str.lower() == address)
    )
    df = df[mask]

    if direction:
        df = df[df['direction'] == direction]

    if start_date:
        df = df[df['timestamp'] >= start_date]

    if end_date:
        df = df[df['timestamp'] <= end_date]

    return df
