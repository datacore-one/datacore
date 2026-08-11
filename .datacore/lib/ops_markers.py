"""Strings that mean `claude -p` did not actually answer, whatever it exited.

TWIN: datacore-app `daemon/datacored/ops_markers.py`. Two repos, so two copies;
they must not drift. The list exists because `claude -p` reports auth failures
on STDOUT with an EMPTY STDERR, and on 2026-08-02..10 a caller that read only
stderr recorded nine days of failures as the empty string while the CoS box
quietly served briefings from a local model. Any new caller of `claude -p` in
this repo checks this list, or it will reproduce that outage.
"""

AUTH_FAILURE_MARKERS = (
    "not logged in",
    "please run /login",
    "credit balance is too low",
    "organization has disabled",
    "subscription access",
    "invalid api key",
    "authentication_error",
    "unauthorized",
)
