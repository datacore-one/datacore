"""
Etherscan transaction fetcher for Personal Finance module.

Fetches on-chain transactions and converts to unified Transaction model.
"""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from .client import EtherscanClient, TOKEN_CONTRACTS
from ..models import Transaction
from ..address_book import lookup_address, get_personal_addresses, is_organization_address
from ..storage import (
    load_transactions,
    save_transactions,
    merge_transactions,
    transactions_to_dataframe
)


def _wei_to_eth(wei: str) -> Decimal:
    """Convert wei to ETH."""
    return Decimal(wei) / Decimal(10**18)


def _token_to_decimal(value: str, decimals: int = 6) -> Decimal:
    """Convert token smallest unit to decimal (USDC has 6 decimals)."""
    return Decimal(value) / Decimal(10**decimals)


def fetch_eth_transactions(address: str) -> List[Transaction]:
    """
    Fetch ETH transactions for an address.

    Args:
        address: Ethereum address

    Returns:
        List of Transaction objects
    """
    client = EtherscanClient()
    raw_txs = client.get_transactions(address)

    transactions = []
    address_lower = address.lower()

    for tx in raw_txs:
        try:
            # Determine direction
            from_addr = tx.get('from', '').lower()
            to_addr = tx.get('to', '').lower()

            if from_addr == address_lower:
                direction = 'out'
            elif to_addr == address_lower:
                direction = 'in'
            else:
                continue  # Skip if address not involved

            # Parse amount
            value_wei = tx.get('value', '0')
            amount = _wei_to_eth(value_wei)

            if amount == 0:
                continue  # Skip zero-value transactions

            # Parse timestamp
            timestamp = datetime.fromtimestamp(int(tx.get('timeStamp', 0)))

            # Resolve counterparty
            counterparty_addr = to_addr if direction == 'out' else from_addr
            counterparty_entry = lookup_address(counterparty_addr)
            counterparty = counterparty_entry.owner if counterparty_entry else None

            # Check if this is a loan to the organization
            is_loan = direction == 'out' and is_organization_address(counterparty_addr)

            # Parse gas fee
            gas_used = int(tx.get('gasUsed', 0))
            gas_price = int(tx.get('gasPrice', 0))
            fee = _wei_to_eth(str(gas_used * gas_price)) if direction == 'out' else None

            transactions.append(Transaction(
                id=f"ethereum_{tx.get('hash')}",
                source='ethereum',
                timestamp=timestamp,
                asset='ETH',
                amount=amount,
                direction=direction,
                tx_type='transfer',
                raw_type='normal',
                from_address=from_addr,
                to_address=to_addr,
                tx_hash=tx.get('hash'),
                fee_amount=fee,
                fee_asset='ETH' if fee else None,
                counterparty=counterparty,
                is_loan=is_loan,
            ))

        except Exception as e:
            print(f"Warning: Could not parse ETH transaction: {e}")

    return transactions


def fetch_token_transactions(
    address: str,
    token: str = 'USDC'
) -> List[Transaction]:
    """
    Fetch ERC-20 token transactions for an address.

    Args:
        address: Ethereum address
        token: Token symbol (USDC, USDT, DAI)

    Returns:
        List of Transaction objects
    """
    if token not in TOKEN_CONTRACTS:
        raise ValueError(f"Unknown token: {token}")

    client = EtherscanClient()
    raw_txs = client.get_token_transfers(
        address=address,
        contract_address=TOKEN_CONTRACTS[token]
    )

    transactions = []
    address_lower = address.lower()

    # Token decimals (USDC/USDT = 6, DAI = 18)
    decimals = 18 if token == 'DAI' else 6

    for tx in raw_txs:
        try:
            # Determine direction
            from_addr = tx.get('from', '').lower()
            to_addr = tx.get('to', '').lower()

            if from_addr == address_lower:
                direction = 'out'
            elif to_addr == address_lower:
                direction = 'in'
            else:
                continue

            # Parse amount
            value = tx.get('value', '0')
            amount = _token_to_decimal(value, decimals)

            if amount == 0:
                continue

            # Parse timestamp
            timestamp = datetime.fromtimestamp(int(tx.get('timeStamp', 0)))

            # Resolve counterparty
            counterparty_addr = to_addr if direction == 'out' else from_addr
            counterparty_entry = lookup_address(counterparty_addr)
            counterparty = counterparty_entry.owner if counterparty_entry else None

            # Check if this is a loan to the organization
            is_loan = direction == 'out' and is_organization_address(counterparty_addr)

            transactions.append(Transaction(
                id=f"ethereum_{tx.get('hash')}_{token}",
                source='ethereum',
                timestamp=timestamp,
                asset=token,
                amount=amount,
                direction=direction,
                tx_type='transfer',
                raw_type='token_transfer',
                from_address=from_addr,
                to_address=to_addr,
                tx_hash=tx.get('hash'),
                counterparty=counterparty,
                is_loan=is_loan,
            ))

        except Exception as e:
            print(f"Warning: Could not parse {token} transaction: {e}")

    return transactions


def fetch_all_transactions(address: str) -> List[Transaction]:
    """
    Fetch all transactions (ETH + USDC) for an address.

    Args:
        address: Ethereum address

    Returns:
        List of Transaction objects
    """
    print(f"Fetching ETH transactions for {address[:10]}...")
    eth_txs = fetch_eth_transactions(address)

    print(f"Fetching USDC transactions for {address[:10]}...")
    usdc_txs = fetch_token_transactions(address, 'USDC')

    all_txs = eth_txs + usdc_txs
    # Sort by timestamp descending
    all_txs.sort(key=lambda x: x.timestamp, reverse=True)

    return all_txs


def sync_ethereum_transactions(addresses: Optional[List[str]] = None) -> int:
    """
    Sync on-chain transactions to local storage.

    Args:
        addresses: List of addresses to sync. Defaults to personal addresses.

    Returns:
        Number of new transactions added
    """
    # Get addresses to sync
    if addresses is None:
        addresses = get_personal_addresses()

    if not addresses:
        print("No personal addresses configured for Ethereum sync.")
        return 0

    # Load existing transactions
    existing_df = load_transactions('ethereum')
    initial_count = len(existing_df)

    # Fetch transactions for each address
    all_new_txs = []
    for addr in addresses:
        print(f"\nSyncing address: {addr[:10]}...")
        txs = fetch_all_transactions(addr)
        all_new_txs.extend(txs)
        print(f"  Found {len(txs)} transactions")

    if not all_new_txs:
        print("\nNo Ethereum transactions found.")
        return 0

    # Convert to DataFrame
    new_df = transactions_to_dataframe(all_new_txs)

    # Merge and save
    merged_df = merge_transactions(existing_df, new_df)
    save_transactions(merged_df, 'ethereum')

    new_count = len(merged_df) - initial_count
    print(f"\nAdded {new_count} new Ethereum transactions.")
    return new_count


def get_transfers_to_organization() -> List[Transaction]:
    """
    Get all outbound transfers to organization addresses.

    These are potential loan disbursements.
    """
    df = load_transactions('ethereum')
    if df.empty:
        return []

    # Filter to outbound transfers marked as loans
    loans = df[
        (df['direction'] == 'out') &
        (df['is_loan'] == True)
    ]

    from ..storage import dataframe_to_transactions
    return dataframe_to_transactions(loans)
