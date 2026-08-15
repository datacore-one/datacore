"""The Datacore core, importable as a package.

Modules could not import the core. 129 module files hand-rolled
`sys.path.insert` to reach it, in at least four different spellings of "where
does the core live" — including `Path(__file__).parent.parent.parent.parent`.
Each spelling is a guess about the machine's layout, and on plur-claw, where the
module tree is `~/Data` and the core is `~/.datacore/v2-runner`, the guess was
wrong. Attestation must never raise, so it returned None and Data's posts went
unrecorded with no error anywhere.

The fix is not a better guess. It is that `import datacore` works, so nobody has
to guess. See DIP-0047.
"""

__all__ = ["ledger"]
