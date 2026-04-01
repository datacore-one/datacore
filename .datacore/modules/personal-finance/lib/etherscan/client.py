"""
Etherscan API client for Personal Finance module.

Fetches on-chain transaction data for Ethereum addresses.
"""

import os
import requests
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
from time import sleep
from dotenv import load_dotenv

# Try to load API key from central env
ENV_PATH = Path.home() / 'Data' / '.datacore' / 'env' / '.env'
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)

# Require API key from environment (no hardcoded fallback)
DEFAULT_API_KEY = os.getenv('ETHERSCAN_API_KEY', '')

# Common token contracts
TOKEN_CONTRACTS = {
    'USDC': '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48',
    'USDT': '0xdAC17F958D2ee523a2206206994597C13D831ec7',
    'DAI': '0x6B175474E89094C44Da98b954EesADE4Fc84D51ebc',
    'WETH': '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2',
}


class EtherscanClient:
    """Etherscan API client for on-chain data (V2 API)."""

    BASE_URL = "https://api.etherscan.io/v2/api"
    CHAIN_ID = 1  # Ethereum mainnet
    RATE_LIMIT_DELAY = 0.25  # 4 requests per second (free tier: 5/sec)

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize Etherscan client.

        Args:
            api_key: Etherscan API key. Defaults to env or hardcoded key.
        """
        self.api_key = api_key or DEFAULT_API_KEY

    def _request(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Make API request with rate limiting."""
        params['chainid'] = self.CHAIN_ID
        params['apikey'] = self.api_key

        try:
            response = requests.get(self.BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()

            if data.get('status') == '0' and data.get('message') != 'No transactions found':
                raise Exception(f"Etherscan API error: {data.get('message')}")

            sleep(self.RATE_LIMIT_DELAY)
            return data

        except requests.RequestException as e:
            raise Exception(f"Etherscan request failed: {e}")

    def get_transactions(
        self,
        address: str,
        start_block: int = 0,
        end_block: int = 99999999
    ) -> List[Dict[str, Any]]:
        """
        Get normal (ETH) transactions for an address.

        Args:
            address: Ethereum address
            start_block: Starting block number
            end_block: Ending block number

        Returns:
            List of transaction records
        """
        params = {
            'module': 'account',
            'action': 'txlist',
            'address': address,
            'startblock': start_block,
            'endblock': end_block,
            'sort': 'desc',
        }

        data = self._request(params)
        return data.get('result', []) if isinstance(data.get('result'), list) else []

    def get_internal_transactions(
        self,
        address: str,
        start_block: int = 0,
        end_block: int = 99999999
    ) -> List[Dict[str, Any]]:
        """Get internal transactions for an address."""
        params = {
            'module': 'account',
            'action': 'txlistinternal',
            'address': address,
            'startblock': start_block,
            'endblock': end_block,
            'sort': 'desc',
        }

        data = self._request(params)
        return data.get('result', []) if isinstance(data.get('result'), list) else []

    def get_token_transfers(
        self,
        address: str,
        contract_address: Optional[str] = None,
        start_block: int = 0,
        end_block: int = 99999999
    ) -> List[Dict[str, Any]]:
        """
        Get ERC-20 token transfers for an address.

        Args:
            address: Ethereum address
            contract_address: Filter by specific token contract (e.g., USDC)
            start_block: Starting block number
            end_block: Ending block number

        Returns:
            List of token transfer records
        """
        params = {
            'module': 'account',
            'action': 'tokentx',
            'address': address,
            'startblock': start_block,
            'endblock': end_block,
            'sort': 'desc',
        }

        if contract_address:
            params['contractaddress'] = contract_address

        data = self._request(params)
        return data.get('result', []) if isinstance(data.get('result'), list) else []

    def get_usdc_transfers(self, address: str) -> List[Dict[str, Any]]:
        """Get USDC token transfers for an address."""
        return self.get_token_transfers(
            address=address,
            contract_address=TOKEN_CONTRACTS['USDC']
        )

    def get_balance(self, address: str) -> int:
        """Get ETH balance in wei."""
        params = {
            'module': 'account',
            'action': 'balance',
            'address': address,
            'tag': 'latest',
        }

        data = self._request(params)
        return int(data.get('result', 0))

    def get_token_balance(self, address: str, contract_address: str) -> int:
        """Get token balance for an address."""
        params = {
            'module': 'account',
            'action': 'tokenbalance',
            'contractaddress': contract_address,
            'address': address,
            'tag': 'latest',
        }

        data = self._request(params)
        return int(data.get('result', 0))
