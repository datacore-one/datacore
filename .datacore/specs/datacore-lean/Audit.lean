import LedgerSpec
open LedgerSpec
#print axioms Hlc.tick_strict
#print axioms Hlc.encode_lt_iff
#print axioms Item.step_edge
#print axioms Item.run_closed
#print axioms Item.dismissed_frozen
#print axioms Item.noop_means_unchanged
#print axioms Item.verify_needs_second_actor
#print axioms Item.completer_is_not_verifier
#print axioms Item.grant_belongs_to_claim
#print axioms Converge.fold_project
#print axioms Converge.converge
#print axioms Chain.fork_visible
#print axioms Chain.resolution_lossless
