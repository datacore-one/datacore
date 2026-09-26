"""MOD-6: Posts, emails and messages leave only when policy or my approval allows, and
every one that leaves is recorded.

Kind: deterministic.
  Recorded -- a sender records what it sent only if it is wrapped by
  datacore.ledger.attests (the wrapper sets __datacore_egress__; egress_runtime_check
  reads the same attribute). Checked on the root senders by importing them for
  real, and on every module-declared sender through egress_runtime_check.py.
  Gated -- the in-flight policy (tool_policy.decide over approvals_policy.yaml +
  tool_effects.yaml) for an agent principal: an email is paused for a grant; a
  public post must be too.

Seeded failure: a sender without @attests (winston_send._post, x_post.post,
oauth_health_check.send_telegram, LateAPIClient._api_request today); or a public post by
an agent that no policy effect covers, so it is allowed without approval (the
effect vocabulary is email.send, payment, prod.deploy -- nothing for posts).
"""
import importlib
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

ROOT_SENDERS = [  # (module, attribute path) -- each sends a message, email or post
    ("winston_send", "_post"),                    # Telegram (Winston's messages)
    ("oauth_health_check", "send_telegram"),      # Telegram alert
    ("x_post", "post"),                           # X post
    ("late_api_wrapper", "LateAPIClient._api_request"),  # social posts through Late
]


@pytest.mark.parametrize("module,attr", ROOT_SENDERS)
def test_every_root_sender_records_what_it_sends(module, attr):
    obj = importlib.import_module(module)
    for part in attr.split("."):
        obj = getattr(obj, part)
    assert getattr(obj, "__datacore_egress__", None), (
        f"{module}.{attr} sends outside Datacore and is not wrapped by @attests, so what it sends "
        "is never recorded")


def test_every_module_declared_sender_is_recording_at_runtime():
    r = subprocess.run([sys.executable, str(LIB / "egress_runtime_check.py")], capture_output=True,
                       text=True, timeout=60, cwd=str(LIB))
    assert r.returncode == 0, f"egress_runtime_check exit {r.returncode}:\n{r.stdout[-1200:]}{r.stderr[-400:]}"
    assert " 0 broken" in r.stdout, r.stdout[-600:]


def test_an_agent_email_is_held_for_approval():
    import tool_policy as P
    d = P.decide("miles", "Bash", {"command": "python3 ~/Data/.datacore/lib/winston_send.py --header x < report"})
    assert not d.allow and d.kind == "cosign", d


def test_an_agent_public_post_is_held_for_approval():
    import tool_policy as P
    d = P.decide("miles", "Bash", {"command": "python3 ~/Data/.datacore/lib/x_post.py --space plur 'We shipped it'"})
    assert not d.allow, (
        f"an agent posting publicly to X is allowed with no approval and no policy effect covers it: {d}")
