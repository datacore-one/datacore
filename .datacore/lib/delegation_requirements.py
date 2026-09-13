"""Concrete execution requirements shared by reviewers and executors.

These fields describe executable work; they never grant execution authority.
Legacy ACCEPTANCE_CRITERIA remains a supported spelling of DONE_WHEN.
"""


def execution_gaps(properties):
    def stated(key):
        value = properties.get(key)
        return value.strip() if isinstance(value, str) else ''
    missing = []
    if stated('SURFACE').lower() in ('', 'unassigned'):
        missing.append('SURFACE')
    if not any(stated(key) for key in ('DONE_WHEN', 'ACCEPTANCE_CRITERIA')):
        missing.append('DONE_WHEN')
    return missing
