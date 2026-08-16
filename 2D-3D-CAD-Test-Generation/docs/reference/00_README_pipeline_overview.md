# 2D→3D Auto Pipeline — Reference Document Index

## Purpose
This document set is context for an AI coding agent (Claude Code / Claude API) whose job is:
**Input:** a 2D engineering drawing (PDF, image, or DXF/DWG) → **Output:** a working SolidWorks 3D part built via API automation.

Read this file first. It tells you what each document covers and the order of operations.

## Document map
| # | File | Covers |
|---|------|--------|
| 01 | `01_language_choice_csharp_over_vba.md` | Why to write C# standalone apps instead of VBA macros; project setup |
| 02 | `02_solidworks_api_fundamentals.md` | Object model, units (critical!), selection, lifecycle |
| 03 | `03_macro_writing_best_practices.md` | Code patterns that work, patterns that fail, anti-patterns from recorded macros |
| 04 | `04_reading_engineering_drawings.md` | Views, projection angle, title block, dimensions, tolerances, GD&T |
| 05 | `05_2d_to_3d_interpretation_strategy.md` | How to convert orthographic views into a feature tree plan |
| 06 | `06_geometry_construction_playbook.md` | API recipes: sketches, extrudes, revolves, holes, fillets, patterns |
| 07 | `07_validation_and_self_checking.md` | How the agent verifies its own model (mass props, dimension checks, rebuild errors) |
| 08 | `08_autonomous_error_handling.md` | Decision policy: retry, guess, log, or escalate |
| 09 | `09_user_interaction_protocol.md` | When and how to ask the human — only at the very end, with an assumption report |
| 10 | `10_common_failure_modes.md` | Known crashes, COM issues, geometry failures and their fixes |

## Order of operations (the pipeline loop)
1. **Parse the drawing** (doc 04). Extract views, dimensions, notes, title block metadata.
2. **Plan the feature tree** (doc 05). Write the plan as structured data (JSON) BEFORE writing any code.
3. **Generate the build program** (docs 01, 02, 03, 06). One C# program per part.
4. **Run and validate** (doc 07). Compare model measurements against drawing dimensions.
5. **Self-repair** (doc 08). Fix rebuild errors and dimension mismatches autonomously, up to the retry budget.
6. **Report to the user** (doc 09). Present the finished part + an assumption report. Ask questions ONLY here, and only about things you could not resolve.

## Non-negotiable rules
- **Plan before code.** Never start emitting API calls without a written feature plan.
- **All SolidWorks API geometry is in METERS**, regardless of the drawing's units. Convert once, at the boundary, with a single helper function. (See doc 02.)
- **Never use recorded-macro style code** (SendKeys, screen coordinates, blind `SelectByID2` with magic names copied from a recording). Recorded macros are for discovering API calls, not for production.
- **Every run must produce a log and an assumptions list.** Silent guesses are forbidden; logged guesses are encouraged.
- **The user is interrupted at most once, at the end.** Make your best educated guess for every ambiguity, build the part, then present the guesses for confirmation.
