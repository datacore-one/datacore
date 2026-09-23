import Lean
import LedgerSpec
import DatacoreSpec
open Lean Elab Command

/-- Every theorem under LedgerSpec/DatacoreSpec may depend only on Lean's
three standard axioms. Prints a count and any offender. -/
elab "#audit_all" : command => do
  let env ← getEnv
  let ok : List Lean.Name := [``propext, ``Classical.choice, ``Quot.sound]
  let mut n := 0
  let mut bad : Array (Lean.Name × Array Lean.Name) := #[]
  for (nm, ci) in env.constants.toList do
    let isOurs := (`LedgerSpec).isPrefixOf nm || (`DatacoreSpec).isPrefixOf nm
    let isThm : Bool := match (ci : Lean.ConstantInfo) with
      | Lean.ConstantInfo.thmInfo _ => true
      | _ => false
    if isOurs && isThm then
      n := n + 1
      let axs : Array Lean.Name ← Lean.collectAxioms nm
      let extra : Array Lean.Name := axs.filter (fun a => !ok.contains a)
      if Array.size extra > 0 then bad := bad.push (nm, extra)
  logInfo m!"theorems audited: {n}; offenders: {bad.size} {bad.toList}"

#audit_all
