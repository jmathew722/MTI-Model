"""Iteration 15b — the A/B that justifies changing pipeline code.

The iteration-15 sweep stopped at its first success (Options=0), so it never
tried Options=4 on that same part. Before editing `build_chamfer` I need the two
values compared on ONE part, alternating so a stale-state explanation cannot
hide in the ordering.
"""
from __future__ import annotations

import json

from swlab import RESULTS
from t11_chamfer_signature import DOC, attempt, build_part, lab

if __name__ == "__main__":
    build_part()
    out = []
    for opts in (4, 0, 4, 0):
        built, detail = attempt(opts, 1)
        out.append({"options": opts, "built": built, "detail": detail})
        print(f"  Options={opts} -> {'BUILT' if built else 'None '}   {detail}", flush=True)
    four = [r["built"] for r in out if r["options"] == 4]
    zero = [r["built"] for r in out if r["options"] == 0]
    verdict = ("Options=4 never builds, Options=0 always does - the pipeline's "
               "Options=4 is the defect" if not any(four) and all(zero) else
               "inconclusive: " + ", ".join(f"{r['options']}={r['built']}" for r in out))
    print(f"\nverdict: {verdict}")
    out.append({"verdict": verdict})
    (RESULTS / "tier11b_chamfer_ab.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    lab.close(DOC)
