"""
Data models for Personal Finance module.

Provides unified transaction format across all sources (Kraken, Gate.io, Ethereum)
and loan tracking models.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional, Literal, List
from pydantic import BaseModel, Field


class Transaction(BaseModel):
    """Unified transaction record from any source."""

    # Identity
    id: str = Field(..., description="Unique ID: source_txhash or source_refid")
    source: Literal['kraken', 'gateio', 'ethereum'] = Field(..., description="Data source")

    # Timing
    timestamp: datetime = Field(..., description="Transaction timestamp (UTC)")

    # Asset details
    asset: str = Field(..., description="Normalized symbol: ETH, USDC, BTC")
    amount: Decimal = Field(..., description="Transaction amount (always positive)")
    direction: Literal['in', 'out'] = Field(..., description="Fund flow direction")

    # Value tracking
    usd_value: Optional[Decimal] = Field(None, description="USD value at time of tx")
    usd_price: Optional[Decimal] = Field(None, description="USD price per unit at time of tx")

    # Transaction type
    tx_type: str = Field(..., description="Normalized type: deposit, withdrawal, trade, transfer, fee")
    raw_type: Optional[str] = Field(None, description="Original type from source")

    # Addresses (for on-chain transactions)
    from_address: Optional[str] = Field(None, description="Sender address")
    to_address: Optional[str] = Field(None, description="Recipient address")
    tx_hash: Optional[str] = Field(None, description="On-chain transaction hash")

    # Fees
    fee_amount: Optional[Decimal] = Field(None, description="Fee amount")
    fee_asset: Optional[str] = Field(None, description="Fee asset symbol")

    # Classification (set during processing)
    counterparty: Optional[str] = Field(None, description="Resolved counterparty name from address book")
    is_loan: bool = Field(False, description="Flagged as loan disbursement")
    loan_id: Optional[str] = Field(None, description="Associated loan ID if is_loan")

    # Metadata
    notes: Optional[str] = Field(None, description="User notes")
    tags: List[str] = Field(default_factory=list, description="Classification tags")


class Loan(BaseModel):
    """Loan record tracking funds lent to external parties."""

    id: str = Field(..., description="Unique loan ID: loan-counterparty-YYYY-NNN")
    status: Literal['active', 'partial_repaid', 'repaid', 'written_off'] = Field(
        'active', description="Loan status"
    )

    # Recipient
    counterparty: str = Field(..., description="Counterparty name from address book")
    counterparty_address: Optional[str] = Field(None, description="Primary recipient address")

    # Principal
    principal_asset: str = Field(..., description="Asset symbol: USDC, ETH, SOL")
    principal_amount: Decimal = Field(..., description="Original loan amount")
    principal_usd: Optional[Decimal] = Field(None, description="USD value at loan time")

    # Timing
    date_issued: datetime = Field(..., description="Date loan was disbursed")
    date_due: Optional[datetime] = Field(None, description="Expected repayment date")

    # Repayment tracking
    amount_repaid: Decimal = Field(Decimal('0'), description="Total repaid in principal asset")
    repayment_transactions: List[str] = Field(
        default_factory=list, description="Transaction IDs of repayments"
    )

    # Disbursement tracking
    disbursement_transactions: List[str] = Field(
        default_factory=list, description="Transaction IDs of disbursements"
    )

    # Metadata
    purpose: Optional[str] = Field(None, description="Loan purpose/reason")
    notes: Optional[str] = Field(None, description="Additional notes")
    tags: List[str] = Field(default_factory=list, description="Classification tags")

    @property
    def outstanding(self) -> Decimal:
        """Calculate outstanding amount."""
        return self.principal_amount - self.amount_repaid

    @property
    def is_fully_repaid(self) -> bool:
        """Check if loan is fully repaid."""
        return self.amount_repaid >= self.principal_amount


class BalanceSnapshot(BaseModel):
    """Point-in-time balance across a single source."""

    timestamp: datetime = Field(..., description="Snapshot timestamp")
    source: str = Field(..., description="Source: kraken, gateio, wallet_0x...")
    asset: str = Field(..., description="Asset symbol")
    balance: Decimal = Field(..., description="Balance amount")
    usd_value: Optional[Decimal] = Field(None, description="USD value at snapshot time")


class AddressEntry(BaseModel):
    """Known address with metadata for counterparty resolution."""

    address: str = Field(..., description="Ethereum address or internal identifier")
    chain: Literal['ethereum', 'solana', 'internal'] = Field(
        'ethereum', description="Blockchain or 'internal' for exchanges"
    )
    label: str = Field(..., description="Human-readable label")
    owner: Optional[str] = Field(None, description="Entity/organization name")
    type: Literal['personal', 'exchange', 'multisig', 'contract', 'external'] = Field(
        'external', description="Address type"
    )
    is_self: bool = Field(False, description="True for addresses owned by user")
    tags: List[str] = Field(default_factory=list, description="Classification tags")
    notes: Optional[str] = Field(None, description="Additional notes")


# Asset normalization mappings
KRAKEN_ASSET_MAP = {
    'XXBT': 'BTC',
    'XETH': 'ETH',
    'ZUSD': 'USD',
    'ZEUR': 'EUR',
    'USDC': 'USDC',
    'USDT': 'USDT',
    'SOL': 'SOL',
    'DOT': 'DOT',
}


def normalize_asset(asset: str, source: str = 'kraken') -> str:
    """Normalize asset symbol to standard format."""
    if source == 'kraken':
        # Remove Kraken-specific prefixes
        return KRAKEN_ASSET_MAP.get(asset, asset.lstrip('X').lstrip('Z'))
    return asset
