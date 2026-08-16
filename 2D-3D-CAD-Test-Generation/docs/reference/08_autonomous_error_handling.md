# 08 — Autonomous Error Handling & Decision Policy

Design intent: **the agent resolves everything it can alone**, makes educated, logged guesses for the rest, and involves the human exactly once — at the end (doc 09). "Ask the user" is the LAST rung of the ladder, not the first.

## The escalation ladder (apply in order, per problem)
1. **Deterministic fix** — compile error, wrong argument count, missing null-check, unit slip. Fix the code. No guess involved.
2. **Consult references** — API help / this doc set / recorded-macro discovery (doc 03) for unknown call signatures.
3. **Alternative construction** — same geometry, different API path (doc 06 fallbacks: plain cut instead of Hole Wizard, explicit circles instead of pattern feature, offset plane instead of face selection). Prefer switching construction over fighting a flaky call.
4. **Educated guess with logged assumption** — for genuine ambiguity in the DRAWING (not in the code). Apply the defaults table below, record `{what, options considered, choice, reason, confidence}`.
5. **Build both / build the likely one** — if two interpretations are plausible and cheap, build the more standard one and note the alternative so the user can flip it with one answer.
6. **Defer to user AT THE END** — only for ambiguities that materially change the part and cannot be reasonably defaulted, and only after the best-guess part is already built.

Hard-stop exception (the only mid-run user contact allowed): the drawing is unreadable/incomplete to the point that no plan can be formed (missing views, illegible scan, no dimensions). Stop early rather than fabricate a part.

## Retry budget & loop control
- **Per feature step:** max 3 attempts (original + 2 alternative constructions). Then mark step FAILED, skip dependents, continue independent steps, report at end.
- **Per build (full plan):** max 3 build-validate-repair cycles. If validation still fails, deliver the best model achieved + a clear failure report. Never loop indefinitely.
- **Never retry the identical call with identical arguments.** Each retry must change something (arguments, construction method, or precondition), and the log must say what changed and why.
- If SolidWorks itself becomes unstable (COM errors on every call, hung process): save what exists, kill and restart the SW process ONCE, reload the saved model or replay the plan; if instability recurs, abort with a diagnostic report.

## Debug information to gather on any feature failure
1. The exact arguments passed (post-conversion values AND original drawing values).
2. Return value / HRESULT / `GetLastError`-style info where available.
3. Sketch status if sketch-related: is it closed? self-intersecting? fully inside the target body? (`ISketch.GetSketchContours` count is a quick closed-profile check.)
4. Model state: body count, last successful feature, rebuild errors.
5. A saved snapshot `.SLDPRT` of the partial model (timestamped) — enables post-mortem without rerunning.

## Common failure → likely cause map (check these FIRST)
| Symptom | Most likely cause |
|---|---|
| Feature 1000× too big/small | mm passed as meters (or inches unconverted) |
| Extrude/cut returns null | Sketch not closed, sketch still open (missing 2nd `InsertSketch`), or nothing selected |
| Cut removes nothing | Wrong direction — flip dir flag or use both-directions/through-all |
| Part mirrored vs drawing | Projection angle misread (first vs third) |
| Revolve fails | Profile crosses the axis, or ≠1 centerline in sketch |
| Fillet fails | Radius ≥ local wall/edge size; reduce radius or skip + report |
| Pattern silently missing | Wrong selection Marks |
| `SelectByID2` false | Name wrong (renaming discipline violated) or entity type string wrong |
| Two bodies after boss | `merge` flag false, or boss doesn't touch base solid (position error) |
| COM cast exception at startup | Interop version mismatch / `EmbedInteropTypes` — see doc 01 |

## Defaults table for drawing ambiguities (rung 4 guesses)
| Ambiguity | Default | Confidence |
|---|---|---|
| No units stated | mm if metric-looking values & ISO block; else inch | med — always report |
| No projection symbol | Third angle if ANSI/inch, else first angle | med — validate with silhouette check |
| Hole depth unstated, hidden lines full height | THRU ALL | high |
| Thread callout, no thread modeling requirement | Model tap-drill Ø, note thread | high |
| "Break sharp edges" only | Skip cosmetic breaks, note it | high |
| Missing locating dim, feature looks centered | Centered on symmetry line | med |
| Fillet radius unlabeled, others are R2 | R2 ("TYP" inference) | med |
| Tolerance only | Model nominal | high |

Every applied default is appended to `assumptions[]` — this list IS the agenda for the single end-of-run user interaction.

## Mindset rules for the agent
- A failed API call is a **fact-finding opportunity**, not a reason to switch strategies randomly. Diagnose → smallest change → retry.
- Distinguish **code bugs** (fix silently, no user involvement ever) from **drawing ambiguity** (default + log) — users should never be asked about problems in the agent's own code.
- Silent guessing is the cardinal sin. Guess boldly, log religiously.
- Deliver SOMETHING: a 90%-correct part with a precise list of the uncertain 10% is a success; an aborted run with "it was ambiguous" is a failure.
