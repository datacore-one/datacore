"""
Address book management for Personal Finance module.

Imports addresses from existing exchange-tools/settings.py and extends
with module-specific addresses for loan tracking.
"""

import sys
from pathlib import Path
from typing import Dict, Optional, List
import yaml

from .models import AddressEntry

# Path to existing address book
EXCHANGE_TOOLS_DIR = Path.home() / 'Data' / '0-personal' / 'code' / 'trading' / 'exchange-tools'
MODULE_DIR = Path(__file__).parent.parent
DATA_DIR = MODULE_DIR / 'data'

# Cache for loaded addresses
_address_cache: Dict[str, AddressEntry] = {}


def _import_from_settings() -> Dict[str, AddressEntry]:
    """Import addresses from existing exchange-tools/settings.py."""
    entries = {}

    settings_path = EXCHANGE_TOOLS_DIR / 'settings.py'
    if not settings_path.exists():
        print(f"Warning: settings.py not found at {settings_path}")
        return entries

    # Add directory to path temporarily to import
    sys.path.insert(0, str(EXCHANGE_TOOLS_DIR))
    try:
        # Import the settings module
        import importlib.util
        spec = importlib.util.spec_from_file_location("exchange_settings", settings_path)
        exchange_settings = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(exchange_settings)

        # Process ORGANIZATION_MULTISIGS
        if hasattr(exchange_settings, 'ORGANIZATION_MULTISIGS'):
            for addr, label in exchange_settings.ORGANIZATION_MULTISIGS.items():
                entries[addr.lower()] = AddressEntry(
                    address=addr.lower(),
                    chain='ethereum',
                    label=label,
                    owner='Organization',
                    type='multisig',
                    is_self=False,
                    tags=['organization', 'loan-recipient']
                )

        # Process ETH_ACCOUNTS
        if hasattr(exchange_settings, 'ETH_ACCOUNTS'):
            for addr, label in exchange_settings.ETH_ACCOUNTS.items():
                # Determine if it's a personal account
                is_personal = 'Organization' not in label
                entries[addr.lower()] = AddressEntry(
                    address=addr.lower(),
                    chain='ethereum',
                    label=label,
                    owner='Owner' if is_personal else 'Organization',
                    type='personal' if is_personal else 'external',
                    is_self=is_personal,
                    tags=['personal'] if is_personal else ['organization']
                )

        # Process EXTERNAL_ACCOUNTS
        if hasattr(exchange_settings, 'EXTERNAL_ACCOUNTS'):
            for addr, label in exchange_settings.EXTERNAL_ACCOUNTS.items():
                is_kraken = 'Kraken' in label
                entries[addr.lower()] = AddressEntry(
                    address=addr.lower(),
                    chain='ethereum',
                    label=label,
                    owner='Kraken' if is_kraken else None,
                    type='exchange' if is_kraken else 'external',
                    is_self=False,
                    tags=['kraken'] if is_kraken else []
                )

    except Exception as e:
        print(f"Warning: Could not import from settings.py: {e}")
    finally:
        sys.path.pop(0)

    return entries


def _load_module_addresses() -> Dict[str, AddressEntry]:
    """Load additional addresses from module's address_book.yaml."""
    entries = {}

    yaml_path = DATA_DIR / 'address_book.yaml'
    if not yaml_path.exists():
        return entries

    try:
        with open(yaml_path) as f:
            data = yaml.safe_load(f)

        if data and 'addresses' in data:
            for addr, info in data['addresses'].items():
                entries[addr.lower()] = AddressEntry(
                    address=addr.lower(),
                    chain=info.get('chain', 'ethereum'),
                    label=info.get('label', addr[:10] + '...'),
                    owner=info.get('owner'),
                    type=info.get('type', 'external'),
                    is_self=info.get('is_self', False),
                    tags=info.get('tags', []),
                    notes=info.get('notes')
                )

    except Exception as e:
        print(f"Warning: Could not load address_book.yaml: {e}")

    return entries


def load_address_book(force_reload: bool = False) -> Dict[str, AddressEntry]:
    """
    Load complete address book from all sources.

    Sources (in order of precedence):
    1. Module's address_book.yaml (overrides others)
    2. exchange-tools/settings.py (existing infrastructure)
    """
    global _address_cache

    if _address_cache and not force_reload:
        return _address_cache

    # Start with settings.py addresses
    addresses = _import_from_settings()

    # Override/extend with module addresses
    module_addresses = _load_module_addresses()
    addresses.update(module_addresses)

    _address_cache = addresses
    return addresses


def lookup_address(address: str) -> Optional[AddressEntry]:
    """Look up address in the address book."""
    addresses = load_address_book()
    return addresses.get(address.lower())


def resolve_counterparty(address: str) -> Optional[str]:
    """Resolve address to counterparty name."""
    entry = lookup_address(address)
    if entry:
        return entry.owner or entry.label
    return None


def get_addresses_by_owner(owner: str) -> List[AddressEntry]:
    """Get all addresses for an owner."""
    addresses = load_address_book()
    return [entry for entry in addresses.values() if entry.owner == owner]


def get_addresses_by_tag(tag: str) -> List[AddressEntry]:
    """Get all addresses with a specific tag."""
    addresses = load_address_book()
    return [entry for entry in addresses.values() if tag in entry.tags]


def get_loan_recipient_addresses() -> List[str]:
    """Get addresses tagged as loan recipients."""
    entries = get_addresses_by_tag('loan-recipient')
    return [e.address for e in entries]


def get_personal_addresses() -> List[str]:
    """Get addresses marked as personal/self."""
    addresses = load_address_book()
    return [addr for addr, entry in addresses.items() if entry.is_self]


def is_organization_address(address: str) -> bool:
    """Check if address belongs to the organization."""
    entry = lookup_address(address)
    if entry:
        return entry.owner == 'Organization' or 'organization' in entry.tags
    return False


def save_module_addresses(entries: Dict[str, AddressEntry]) -> None:
    """Save addresses to module's address_book.yaml."""
    yaml_path = DATA_DIR / 'address_book.yaml'

    data = {'addresses': {}}
    for addr, entry in entries.items():
        data['addresses'][addr] = {
            'chain': entry.chain,
            'label': entry.label,
            'owner': entry.owner,
            'type': entry.type,
            'is_self': entry.is_self,
            'tags': entry.tags,
            'notes': entry.notes
        }

    with open(yaml_path, 'w') as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    # Clear cache to force reload
    global _address_cache
    _address_cache = {}
