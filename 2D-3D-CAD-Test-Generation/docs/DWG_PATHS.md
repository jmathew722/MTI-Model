# The two DWG paths — decision, routing, and parity

**Status:** DECIDED 2026-08-15. Resolves REFACTOR_ANALYSIS §1.6 / §2.4, which
flagged `dwg_native/` as "the costliest redundancy in the system" *because it sat
in an ambiguous middle state* — not because a second path is wrong.

**Decision: option (a). `dwg_native/` is a real, permanent second product.** It
is not absorbed into the vision pipeline, and it is not deleted. What changes is
that the ambiguity is gone: each path has a stated job, a stated entry point, an
explicit routing rule in code (`pipeline/dwg_routing.py`), its own guiding
principles (below), and a parity checklist for changes that must land in both.

---

## Why not absorb it (the option-(b) case, and why it loses)

Absorbing `dwg_native/` as "another extraction front-end feeding the existing
resolver → build_sequencer → macro_generator chain" is attractive on paper: one
build chain, one set of invariants. It loses because **the two products disagree
about what the source of truth IS**, which is a difference in kind, not in
plumbing:

| | Vision pipeline | DWG-native pipeline |
|---|---|---|
| Truth | what the DRAWING SHOWS (a human-readable engineering document) | what the DWG ENTITIES ARE (exact geometry) |
| Core problem | reading ambiguity — resolve it, never block | import fidelity — gate it, fail loudly |
| Ambiguity policy | *always* produce a complete approximate model | a part that fails a gate goes to `failed/` |
| Runs on | any OS (COM build optional) | Windows + SolidWorks only |
| Entry point | `main.py`, webapp `:8092` | `run-dwg.ps1`, webapp `:8095` |
| Vision model | yes (every stage) | none |

The vision pipeline's central guarantee — *a complete approximate model is always
the correct outcome* — is exactly wrong for the native path, where an exact
import that does not match the DWG is a defect, not something to resolve and flag.
Merging them means one of the two guarantees gets weakened by the merge, and the
weakened one is load-bearing for its product.

---

## Guiding principles — `dwg_native/`

These are the native path's own, and they deliberately differ from
[CLAUDE.md](../CLAUDE.md)'s:

1. **The entities are the truth.** No value is ever inferred, resolved, or
   averaged. If the DWG does not say it, the pipeline does not know it.
2. **Gate, don't resolve.** Five verification gates; a part that fails one lands
   in `failed/` naming the gate. There is no "complete approximate model" here —
   an approximate model from exact input is a bug.
3. **Provenance on every value** (`assert_provenance`): each mapped number cites
   the entity it came from.
4. **One serialized SolidWorks session** (`session/job_queue.py`): the web layer
   never touches COM; a job id is returned immediately.
5. **Shared modules are IMPORTED, never copied** — `coordinate_normalize`,
   `solidworks_builder` and friends stay canonical (this is already the stated
   rule in `dwg_native/__init__.py`; the parity checklist below is how it is kept
   honest for the modules that are NOT shared).

## Guiding principles — vision pipeline

Unchanged: resolve and flag, never block; never fabricate a number; every feature
ends in a named end-state. See [CLAUDE.md](../CLAUDE.md).

---

## Routing rule (the code is `pipeline/dwg_routing.py`)

> **A DWG entering the vision pipeline stays in the vision pipeline. The
> DWG-native pipeline is entered deliberately, through its own entry point.**

* `main.py --drawing part.dwg` / webapp `:8092` → **vision pipeline**. The DWG is
  converted for the vision read, and **Stage 2.4 `dwg_crosscheck`** gives it the
  geometry-first benefit that is cheap to share: SolidWorks parses the DWG's own
  annotation text and CORRECTS misread OCR digits (16.00 vs 16.80) before the
  resolver. Disabled with `--no-dwg-crosscheck`; a graceful no-op without
  SolidWorks (and reported as such — the vision reading then stands alone).
* `run-dwg.ps1` / webapp `:8095` → **DWG-native pipeline**: import → extract →
  map → build → verify, with the entities as truth.

`route_for(path, entry_point=...)` returns the route, the reason, and whether the
Stage 2.4 cross-check applies — so the CLI, the webapp, and any future caller
answer the question identically instead of each implementing the rule.

---

## Parity checklist — changes that must land in BOTH

`dwg_native/build/vba_emit.py` and `dwg_native/build/builder.py` are separate
emitters from `pipeline/macro_generator.py` / `pipeline/solidworks_builder.py`.
Anything on this list is a **both-paths change**; a PR that touches one without
the other should say why in its description.

| Change | Vision path | Native path |
|---|---|---|
| New geometry-invariant guard (overlap, orientation, dropped position) | `macro_generator._assert_*` | `dwg_native/build/vba_emit.py` |
| Verified SolidWorks API signature change (e.g. `FeatureCircularPattern5`) | `macro_generator`, `solidworks_builder` | `dwg_native/build/builder.py` |
| Coordinate/anchor semantics | `coordinate_normalize` (shared — import it) | imports the same module |
| Unit conversion | `coordinate_normalize.to_meters` (shared) | imports the same module |
| Feature end-state vocabulary | `build_sequencer` dispositions | native gates + `failed/` |
| Per-feature reporting | `pipeline/feature_ledger.py` | not yet wired — see below |

**Known non-parity (recorded, not hidden):** the native path does not yet write
`pipeline/feature_ledger.py` entries, so a native run has no per-feature ledger.
That is the next parity item, not an accident.

---

## What this decision does NOT license

Adding a THIRD DWG path, or growing native-only copies of logic that already
exists in `pipeline/` (the import rule in principle 5 exists to prevent exactly
that). Before adding any new DWG capability, decide from this document which
product owns it — that is what §2.4 asked for.
