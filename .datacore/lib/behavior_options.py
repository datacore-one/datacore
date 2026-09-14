"""Explicit opt-ins for proposed workflow policies, separate from bug fixes.

No option is enabled by an omitted value. Invalid configuration is an error,
not permission to switch policy silently. These are product behavior settings,
not an authentication or operating-system boundary.
"""
import os

OPTIONS = {
    'review_before_execution': 'DATACORE_REVIEW_BEFORE_EXECUTION',
    'cadence_proposals': 'DATACORE_CADENCE_PROPOSALS',
    'instance_bound_execution': 'DATACORE_INSTANCE_BOUND_EXECUTION',
}


def enabled(name):
    variable = OPTIONS[name]
    value = os.environ.get(variable, '0')
    if value not in ('0', '1'):
        raise ValueError(f'{variable} must be 0 or 1')
    return value == '1'
