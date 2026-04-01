---
summary: "Personal crypto finance tracking — holdings, transactions, and loan reconciliation across exchanges and on-chain."
triggers: ["personal finance", "crypto holdings", "loan status", "sync transactions", "check loans"]
context: on_match
---

# Personal Finance Module

## Purpose

Tracks personal cryptocurrency holdings across Kraken, Gate.io, and Ethereum on-chain. Reconciles loans given to external parties. Provides a combined view of balances, transaction history, and loan status.

## Quick Start

> Say "check loans" to view the loan dashboard, or "/personal-finance sync" to fetch latest transactions.

## How It Works

### Data Sources

| Source | Type | Integration |
|--------|------|-------------|
| Kraken | Exchange API | `lib/kraken/` |
| Gate.io | Exchange API | `lib/gateio/` (shared with trading module) |
| Ethereum | On-chain | `lib/etherscan/` (Etherscan API) |

### Features

- **Transaction Sync** — fetch history from all sources into Parquet stores
- **Loan Tracking** — identify and track loans by counterparty, amount, asset, date
- **Holdings View** — combined balance across all sources
- **Address Book** — resolve addresses to counterparties (imports from trading module)

## Agents & Commands

| Name | Type | When to use |
|------|------|-------------|
| `loan-tracker` | agent | Loan identification and tracking |
| `/personal-finance` | command | Main interface: loans, sync, holdings, report |

## Key Paths

| Path | Purpose |
|------|---------|
| `data/transactions/` | Transaction history (Parquet) |
| `data/loans/known_loans.yaml` | Pre-defined loans |
| `data/address_book.yaml` | Additional address resolution |
| `lib/` | models, storage, kraken/, gateio/, etherscan/ |

## Setup

API keys in `.datacore/env/.env` or exchange-specific key files. See `module.yaml` for env var names.

---

*This file covers structure, capability, and stable configuration. Learned behavior, user corrections, and operational preferences live as engrams — call `plur_recall_hybrid` for those.*
