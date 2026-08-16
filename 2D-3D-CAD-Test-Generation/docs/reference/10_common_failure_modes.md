# 10 — Common Failure Modes, Environment & COM Gotchas

A field guide to problems that are NOT the agent's logic but will look like it.

## Environment / setup failures
| Symptom | Cause | Fix |
|---|---|---|
| `CreateInstance` hangs or SW splash never finishes | SW first-run dialogs, license prompt, or previous crashed instance | Kill stray `SLDWORKS.exe`; launch SW manually once per machine to clear dialogs; then attach |
| `InvalidCastException` casting to `ISldWorks` | Interop DLL version ≠ installed SW; `EmbedInteropTypes=true` | Reference the DLLs from the INSTALLED SW's `api\redist`; set EmbedInteropTypes=false |
| `GetActiveObject` not found (compile) | Targeting .NET 6/8 | Target .NET Framework 4.8 (doc 01) |
| Calls fail with `RPC_E_SERVERCALL_RETRYLATER` | SW busy (rebuilding, dialog open) | Register an `IMessageFilter` (COM retry filter) at startup — standard OLE pattern; retry with backoff |
| Everything works run 1, flaky run N | RCW/COM object leaks accumulating | `Marshal.ReleaseComObject` on loop-created objects; null refs; periodic GC.Collect in long batches |
| Macro/automation blocked | SW security settings or PDM add-in interference | Check Tools→Options; disable nonessential add-ins for the automation session |

## Dialog suppression (dialogs = hung unattended runs)
- Never call APIs that open UI. Use `Silent` save options.
- `swApp.SetUserPreferenceToggle` relevant toggles (e.g., confirmation prompts) at session start; restore at end.
- FeatureWorks/“What’s Wrong” dialogs after bad rebuilds can block — validate BEFORE actions that trigger them, and prefer `ForceRebuild3(false)` + programmatic error reading (doc 07) over UI-triggering paths.

## Geometry failures (model-side)
| Symptom | Cause | Fix |
|---|---|---|
| "Zero-thickness geometry" error | Cut/boss leaves surfaces touching at a line/point | Overlap profiles slightly past the boundary (extend cut 0.001 mm beyond) or redesign the step |
| Fillet fails on some edges | Radius too big for local geometry; edge chains split by earlier features | Fillet earlier/larger radii first, or reduce radius, or fillet edge-by-edge and skip failures with a report |
| Boss creates a second body | Doesn't intersect base (position/unit error) or merge=false | Verify positions; assert body count after every additive feature |
| Cut splits part into 2 bodies | Cut fully severs the part — usually depth/position wrong | Re-check the evidence dims for that step |
| Sketch "cannot be used" for feature | Open contour, self-intersection, or duplicate overlapping entities | Check `GetSketchContours`; ensure endpoints coincide EXACTLY (compute, don't accumulate floating point along a chain — derive each vertex from the plan, not from the previous segment) |
| Feature works but wrong direction | Extrude/cut direction flag | Flip flag; or use mid-plane/both-direction conditions which are direction-agnostic |

## Floating point discipline
- Compute every sketch vertex directly from plan dimensions (closed-form), never by chaining additions around a profile — accumulated error leaves micro-gaps that break contours.
- Compare coordinates with a tolerance (1e-9 m) — never `==`.
- Angles: keep exact expressions (`Math.PI/6`), don't round degrees→radians early.

## Version sensitivity
- Feature methods get superseded (`FeatureExtrusion2`→`3`, `FeatureCut3`→`4`...). If a documented call doesn't exist, the installed SW is older/newer than the doc — check the local API help version, or use the earlier-numbered method which usually still exists.
- Hole Wizard signatures are the most version-volatile; the plain-cut fallback (doc 06) is the most stable API surface in the product.

## File/path issues
- `SaveAs3` fails silently with bad paths — pre-create output directories; no illegal filename chars from part names; always check `errs/warns` out-params.
- PDM vaults intercept saves — for this pipeline, save OUTSIDE any vault directory.

## Process hygiene for the batch pipeline
- One part per program invocation is simplest and most robust; keep the SW instance alive across invocations by attaching.
- Watchdog: if a build exceeds a time budget (e.g., 5 min for a simple part), assume a hidden dialog/hang, capture state, kill and restart SW once (doc 08).
- Timestamped snapshots of partial models on every failure — post-mortems without reruns.
- Keep a per-machine `environment.json` (SW version, template paths, output dir) the agent reads instead of asking the user (doc 09).
