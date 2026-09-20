"""The public page's disclosure guard, including the ways it can be wrong.

testnet.datacore.one is reachable by anyone. The generator refuses to write a
page containing anything from this installation's own registries -- principals,
hosts, space names, addresses, emails, versions -- and a guard like that has two
opposite failure modes, both of which end with it being switched off:

  MISSES a real leak        the page publishes a name that should not be public
  REPORTS a leak that is
  not in the page           the page never publishes, the refusal is not
                            believed, and the guard gets removed as broken

Both are here. The second is not hypothetical: masking the approved term "Tris"
before "@TrisHermes_bot" rewrote the handle to "@\\x00Hermes_bot", so the longer
term matched nothing and "hermes" -- a real host -- was reported as leaking from
a page that never contained it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import testnet_dashboard as td  # noqa: E402


def test_an_approved_handle_does_not_shelter_the_host_inside_it():
    """The ordering bug, stated directly.

    "@TrisHermes_bot" is published on purpose; "hermes" is a host name and must
    never appear. Masking has to remove the whole approved handle, or the guard
    reports a leak the page does not have.
    """
    page = "<p>Tris — @TrisHermes_bot — research</p>"
    assert "hermes" not in [h.lower() for h in td.audit(page)]


def test_the_same_word_outside_an_approved_term_is_still_caught():
    """Masking must remove the approved STRING, not the word everywhere.

    If "hermes" were simply dropped from the denylist to let the handle
    through, a genuine mention of the host would publish silently.
    """
    page = "<p>@TrisHermes_bot</p><p>deployed from the hermes gateway</p>"
    assert "hermes" in [h.lower() for h in td.audit(page)]


def test_a_person_is_refused():
    """The rule this whole guard exists for: no names of people."""
    hits = td.audit("<td>gregor</td>")
    assert any("gregor" in h.lower() for h in hits)


@pytest.mark.parametrize("leak,what", [
    ("<p>209.38.243.88</p>", "IPv4 address"),
    ("<p>ops@datacore.one</p>", "email address"),
    ("<p>org-workspace 0.5.2</p>", "version string"),
])
def test_addresses_emails_and_versions_are_refused(leak, what):
    """A version number is an attack aid, not a statistic."""
    assert any(h.startswith(what) for h in td.audit(leak))


def test_a_client_space_name_is_refused():
    """Space names are venture and client names."""
    page = "<td>6-meridian</td>"
    assert td.audit(page), "a space name reached a public page unflagged"


def test_the_generated_date_is_not_mistaken_for_a_version():
    """2026.09.20 is three dot-separated numbers and is not a version.

    A guard that cries wolf on its own timestamp refuses every page it ever
    generates, which is indistinguishable from being broken.
    """
    assert not [h for h in td.audit("<p>2026.09.20</p>") if h.startswith("version")]


def test_the_denylist_is_read_from_the_installation_not_hardcoded():
    """A hardcoded list protects yesterday's data.

    Principals get added, spaces get created, hosts get renamed. A guard that
    does not re-read them passes while the thing it guards against walks past.
    """
    terms = td.forbidden_terms()
    assert terms, "the denylist is empty; the guard would pass anything"
    assert any(len(t) > 3 for t in terms)
