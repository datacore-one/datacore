"""
Loan tracking for Personal Finance module.

Identifies potential loans from transactions and manages loan records.
"""

import yaml
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import List, Optional, Dict, Any

from .models import Loan, Transaction
from .address_book import is_organization_address, lookup_address, get_addresses_by_tag
from .storage import (
    load_loans,
    save_loans,
    loans_to_dataframe,
    dataframe_to_loans,
    get_all_transactions
)

# Module data directory
MODULE_DIR = Path(__file__).parent.parent
LOANS_DIR = MODULE_DIR / 'data' / 'loans'
KNOWN_LOANS_PATH = LOANS_DIR / 'known_loans.yaml'


def load_known_loans() -> List[Loan]:
    """Load pre-defined loans from known_loans.yaml."""
    if not KNOWN_LOANS_PATH.exists():
        return []

    try:
        with open(KNOWN_LOANS_PATH) as f:
            data = yaml.safe_load(f)

        loans = []
        for entry in data.get('loans', []):
            # Parse date
            date_issued = entry.get('date_issued')
            if isinstance(date_issued, str):
                date_issued = datetime.fromisoformat(date_issued)

            date_due = entry.get('date_due')
            if isinstance(date_due, str):
                date_due = datetime.fromisoformat(date_due)

            loan = Loan(
                id=entry.get('id', ''),
                status=entry.get('status', 'active'),
                counterparty=entry.get('counterparty', ''),
                counterparty_address=entry.get('counterparty_address'),
                principal_asset=entry.get('principal_asset', ''),
                principal_amount=Decimal(str(entry.get('principal_amount', 0))),
                principal_usd=Decimal(str(entry['principal_usd'])) if entry.get('principal_usd') else None,
                date_issued=date_issued,
                date_due=date_due,
                amount_repaid=Decimal(str(entry.get('amount_repaid', 0))),
                repayment_transactions=entry.get('repayment_transactions', []),
                disbursement_transactions=entry.get('disbursement_transactions', []),
                purpose=entry.get('purpose'),
                notes=entry.get('notes'),
                tags=entry.get('tags', []),
            )
            loans.append(loan)

        return loans

    except Exception as e:
        print(f"Warning: Could not load known loans: {e}")
        return []


def identify_potential_loans(
    min_usd_value: float = 100,
    counterparties: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """
    Identify potential loans from transaction history.

    A potential loan is:
    - Outbound transfer to a known external address
    - To an organization (not personal)
    - Above minimum threshold

    Args:
        min_usd_value: Minimum USD value to consider
        counterparties: Filter to specific counterparties (e.g., ['Organization'])

    Returns:
        List of potential loan records
    """
    df = get_all_transactions()
    if df.empty:
        return []

    potential_loans = []

    # Filter to outbound transfers
    outbound = df[df['direction'] == 'out']

    for _, tx in outbound.iterrows():
        # Skip if no recipient address
        to_addr = tx.get('to_address')
        if not to_addr:
            continue

        # Look up address
        entry = lookup_address(to_addr)
        if not entry:
            continue

        # Skip personal addresses
        if entry.is_self:
            continue

        # Skip exchanges (these are deposits, not loans)
        if entry.type == 'exchange':
            continue

        # Filter by counterparty if specified
        owner = entry.owner or entry.label
        if counterparties and owner not in counterparties:
            continue

        # Check if already flagged as loan
        if tx.get('is_loan'):
            potential_loans.append({
                'transaction_id': tx['id'],
                'timestamp': tx['timestamp'],
                'asset': tx['asset'],
                'amount': tx['amount'],
                'counterparty': owner,
                'address': to_addr,
                'tx_hash': tx.get('tx_hash'),
                'confidence': 'high',
            })

    return potential_loans


def get_loans_by_counterparty(counterparty: str) -> List[Loan]:
    """Get all loans to a specific counterparty."""
    all_loans = get_all_loans()
    return [loan for loan in all_loans if loan.counterparty == counterparty]


def get_active_loans() -> List[Loan]:
    """Get all active (not fully repaid) loans."""
    all_loans = get_all_loans()
    return [loan for loan in all_loans if loan.status == 'active']


def get_all_loans() -> List[Loan]:
    """Get all loans (known + stored)."""
    # Start with known loans
    loans = load_known_loans()

    # Add any additional stored loans
    stored_df = load_loans()
    if not stored_df.empty:
        stored_loans = dataframe_to_loans(stored_df)
        # Merge, avoiding duplicates by ID
        known_ids = {loan.id for loan in loans}
        for loan in stored_loans:
            if loan.id not in known_ids:
                loans.append(loan)

    return loans


def get_loan_summary() -> Dict[str, Any]:
    """
    Generate loan summary statistics.

    Returns:
        Summary dict with totals by counterparty and asset
    """
    loans = get_all_loans()

    summary = {
        'total_loans': len(loans),
        'active_loans': 0,
        'by_counterparty': {},
        'by_asset': {},
        'total_outstanding_usd': Decimal('0'),
    }

    for loan in loans:
        if loan.status == 'active':
            summary['active_loans'] += 1

        # By counterparty
        cp = loan.counterparty
        if cp not in summary['by_counterparty']:
            summary['by_counterparty'][cp] = {
                'count': 0,
                'assets': {},
            }
        summary['by_counterparty'][cp]['count'] += 1

        # By asset within counterparty
        asset = loan.principal_asset
        if asset not in summary['by_counterparty'][cp]['assets']:
            summary['by_counterparty'][cp]['assets'][asset] = Decimal('0')
        summary['by_counterparty'][cp]['assets'][asset] += loan.outstanding

        # Global by asset
        if asset not in summary['by_asset']:
            summary['by_asset'][asset] = Decimal('0')
        summary['by_asset'][asset] += loan.outstanding

    return summary


def generate_loan_dashboard() -> str:
    """
    Generate a formatted loan dashboard.

    Returns:
        Formatted string for display
    """
    loans = get_all_loans()
    summary = get_loan_summary()

    lines = []
    lines.append("═" * 55)
    lines.append(f"LOAN DASHBOARD - {datetime.now().strftime('%Y-%m-%d')}")
    lines.append("═" * 55)
    lines.append("")

    # Group by counterparty
    by_cp = {}
    for loan in loans:
        cp = loan.counterparty
        if cp not in by_cp:
            by_cp[cp] = []
        by_cp[cp].append(loan)

    for cp, cp_loans in by_cp.items():
        active = [l for l in cp_loans if l.status == 'active']
        if not active:
            continue

        lines.append(f"{cp.upper()} LOANS (Active)")
        lines.append("-" * 30)

        for loan in active:
            amount_str = f"{loan.principal_amount:,.0f} {loan.principal_asset}"
            date_str = loan.date_issued.strftime('%Y-%m-%d') if loan.date_issued else 'Unknown'
            lines.append(f"{loan.id}: {amount_str:>15} | {date_str}")
            if loan.purpose:
                lines.append(f"  Purpose: {loan.purpose}")

        # Subtotal for this counterparty
        lines.append("")
        totals = summary['by_counterparty'].get(cp, {}).get('assets', {})
        for asset, amount in totals.items():
            lines.append(f"  Subtotal {cp}: {amount:,.0f} {asset}")
        lines.append("")

    lines.append("═" * 55)
    lines.append("TOTALS")

    for asset, amount in summary['by_asset'].items():
        lines.append(f"  {asset}: {amount:,.2f}")

    lines.append("")
    lines.append(f"Active loans: {summary['active_loans']} of {summary['total_loans']}")
    lines.append("═" * 55)

    return "\n".join(lines)
