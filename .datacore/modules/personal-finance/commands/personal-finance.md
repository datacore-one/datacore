# /personal-finance

## Command Context

### When to Reference Personal Finance Module

**Always reference when:**
- User asks about crypto holdings or portfolio
- User wants to track loans or money lent
- Need to reconcile transactions across exchanges
- User asks "how much did I lend to [entity]"
- User wants to sync financial data

**Key decisions the module informs:**
- Loan status and outstanding amounts
- Total holdings across sources
- Transaction history for specific addresses

### Quick Reference

| Question | Answer |
|----------|--------|
| Data sources? | Kraken, Gate.io, Ethereum wallets |
| Where is data stored? | `.datacore/modules/personal-finance/data/` |
| How to add loans? | Edit `data/loans/known_loans.yaml` |
| How to add addresses? | Edit `data/address_book.yaml` |

### Agents This Command Invokes

| Agent | Purpose |
|-------|---------|
| loan-tracker | Identify and track loans from transactions |

### Integration Points

- **Address book** - Imports from `0-personal/code/trading/df-kraken/settings.py`
- **Kraken API** - Uses existing `kraken.key` credentials
- **Gate.io** - Uses trading module's credentials
- **Etherscan** - Uses existing API key

---

Personal Finance - single entry point for all personal finance operations.

## Intent Detection

If the user provides context with the command, infer intent:

| User Input | Intent | Action |
|------------|--------|--------|
| `/personal-finance loans` | View loans | Show loan dashboard |
| `/personal-finance sync` | Sync data | Fetch from all sources |
| `/personal-finance holdings` | View holdings | Show current balances |
| `/personal-finance org-loans` | Organization loans | Show loans to organization |
| `/personal-finance report` | Generate report | Create summary report |

If no context or unclear intent, present the menu.

## Menu

```
PERSONAL FINANCE

What would you like to do?

1. **View loan status** - Dashboard of outstanding loans
2. **Sync transactions** - Fetch latest from Kraken, Gate.io, Ethereum
3. **View holdings** - Current balances across all sources
4. **Generate report** - Transaction or holdings summary
```

## Workflows

### 1. Loan Status

Display loan dashboard:

```python
from personal_finance.lib.loan_tracker import generate_loan_dashboard
print(generate_loan_dashboard())
```

Output format:
```
═══════════════════════════════════════════════════════
LOAN DASHBOARD - [Date]
═══════════════════════════════════════════════════════

ORGANIZATION LOANS (Active)
---------------------------
loan-org-2024-001: 7,000 USDC   | 2024-01-15
  Purpose: Ops working capital
loan-org-2024-002: 12,000 USDC  | 2024-03-20
  Purpose: Ops expansion
loan-org-2024-003: 10,000 USDC  | 2024-07-01
  Purpose: From Exchanges account

  Subtotal Organization: 29,000 USDC

SABYASACHI LOANS (Active)
-------------------------
loan-sabyasachi-2024-001: 21.3 SOL | 2024-06-15
  Purpose: June invoice advance

  Subtotal Sabyasachi: 21.3 SOL

═══════════════════════════════════════════════════════
TOTALS
  USDC: 29,000.00
  SOL: 21.30

Active loans: 4 of 4
═══════════════════════════════════════════════════════
```

### 2. Sync Transactions

**Step 1: Confirm sources**

```
Sync data from:
- [x] Kraken (ledger history)
- [x] Gate.io (deposits/withdrawals)
- [x] Ethereum (on-chain for personal wallets)

Proceed? [Y/n]
```

**Step 2: Execute sync**

```python
from personal_finance.lib.kraken.fetcher import sync_kraken_transactions
from personal_finance.lib.gateio.adapter import sync_gateio_transactions
from personal_finance.lib.etherscan.fetcher import sync_ethereum_transactions

kraken_count = sync_kraken_transactions()
gateio_count = sync_gateio_transactions()
eth_count = sync_ethereum_transactions()

print(f"Synced: {kraken_count} Kraken, {gateio_count} Gate.io, {eth_count} Ethereum")
```

**Step 3: Report new loans**

After sync, check for new potential loans:

```python
from personal_finance.lib.loan_tracker import identify_potential_loans
potential = identify_potential_loans(counterparties=['Organization'])
if potential:
    print(f"Found {len(potential)} potential new loans to review")
```

### 3. View Holdings

Display current balances:

```python
from personal_finance.lib.kraken.client import KrakenClient
from personal_finance.lib.gateio.adapter import get_total_balance
from personal_finance.lib.etherscan.client import EtherscanClient

# Kraken
kraken = KrakenClient()
kraken_balance = kraken.get_balance()

# Gate.io
gateio_balance = get_total_balance()

# Ethereum (for configured wallets)
eth_client = EtherscanClient()
# ... fetch balances for each wallet

# Display combined
```

Output format:
```
HOLDINGS SUMMARY - [Date]
═════════════════════════

KRAKEN
------
ETH: 1.234
USDC: 5,000.00

GATE.IO
-------
USDT: 10,000.00

ETHEREUM WALLETS
----------------
0x5C50...E677: 0.5 ETH, 2,000 USDC

TOTAL (USD equivalent)
----------------------
~$XX,XXX
```

### 4. Generate Report

**Options:**
- Holdings summary (markdown)
- Transaction history (CSV export)
- Loan reconciliation (markdown)

## Scripts

Python scripts available in `lib/`:

| Script | Purpose |
|--------|---------|
| `kraken/fetcher.py` | Sync Kraken transactions |
| `gateio/adapter.py` | Sync Gate.io data |
| `etherscan/fetcher.py` | Sync on-chain transactions |
| `loan_tracker.py` | Loan identification and dashboard |
| `address_book.py` | Address resolution |

## Configuration

**Address Book:**
- Imported from: `0-personal/code/trading/exchange-tools/settings.py`
- Module additions: `data/address_book.yaml`

**Known Loans:**
- Location: `data/loans/known_loans.yaml`
- Format:
```yaml
loans:
  - id: "loan-org-2024-001"
    status: active
    counterparty: "Organization"
    principal_asset: "USDC"
    principal_amount: 7000
    date_issued: "2024-01-15"
    purpose: "Description"
```

**Credentials:**
- Kraken: `0-personal/code/trading/exchange-tools/kraken.key`
- Etherscan: `.datacore/env/.env` or hardcoded fallback
- Gate.io: `.datacore/modules/trading/lib/gateio/.env`

## Follow-up Actions

After completing any workflow, offer relevant next steps:

| Completed | Suggest |
|-----------|---------|
| Loan status | "Want to sync latest transactions?" or "Generate a reconciliation report?" |
| Sync | "View updated loan dashboard?" or "Check for new potential loans?" |
| Holdings | "Generate a holdings report for records?" |
| Report | "Open report in editor?" or "Archive to notes?" |

## Auto-Run Mode

If `settings.personal-finance.auto_sync: true`:
- Skip the sync confirmation prompt
- Execute sync immediately for all enabled sources

User can configure in `~/.datacore/settings.local.yaml`:

```yaml
personal-finance:
  auto_sync: false          # Skip sync confirmation
  default_view: loans       # Default to loan dashboard
```

## Error Handling

**API Key Missing:**
```
Kraken API key not found at expected location.

Solution:
  1. Copy your API key to: 0-personal/code/trading/exchange-tools/kraken.key
  2. Or update the path in module settings
```

**Rate Limit Exceeded:**
```
Exchange API rate limit reached.

Solution:
  Wait 60 seconds and retry, or reduce sync_lookback_days setting.
```

**No Transactions Found:**
```
No transactions returned from [source].

Possible causes:
  - API credentials may be incorrect
  - Date range may be outside account history
  - Account may have no activity
```

**Address Not in Book:**
```
Unknown address detected: 0x...

To resolve:
  1. Add to data/address_book.yaml with label and owner
  2. Or ignore if not relevant to loan tracking
```

## Your Boundaries

**YOU CAN:**
- Fetch and display transaction data from configured exchanges
- Show loan status and generate reports
- Resolve addresses using the address book
- Identify potential loans based on transaction patterns
- Create markdown reports for reconciliation

**YOU CANNOT:**
- Execute any trades or transfers
- Modify exchange account settings
- Access accounts not configured in credentials
- Make financial recommendations or predictions
- Automatically mark loans as repaid without user confirmation

**YOU MUST:**
- Ask for confirmation before syncing (unless auto_sync enabled)
- Clearly label loan status (active/partial/repaid)
- Distinguish between confirmed loans and potential loans
- Preserve existing loan records when syncing
