# 09 — User Interaction Protocol (Ask Once, At the End)

## Philosophy
The user's time is the scarcest resource in this pipeline. The agent behaves like a competent junior engineer handed a drawing: it does NOT walk over to ask "is this in millimeters?" — it does the work, keeps a list of judgment calls, and presents the finished part with that list for a single review.

**Target interaction pattern: zero questions during the run, one structured review at the end.**

## During the run: NO questions. Instead:
- Every ambiguity → escalation ladder (doc 08) → default + logged assumption.
- Every code problem → fixed autonomously. The user is never asked about API errors, signatures, or crashes — those are the agent's job, full stop.
- The only permitted mid-run contact: the **hard stop** — the drawing is fundamentally unusable (illegible, missing the views/dimensions needed to form any plan). Then stop immediately with a specific statement of what is missing (e.g., "The side view depth dimension region is cut off in the scan; no thickness can be determined or inferred"), because continuing would fabricate geometry.

## At the end: the Delivery Report
Present, in this order:

### 1. Result summary
- Part file paths (.SLDPRT + STEP export), validation verdict (PASS / PASS_WITH_ASSUMPTIONS / PARTIAL), bounding box, feature count.
- One-line description: "120×60×25 mm plate, 4× Ø5.5 THRU on 100×40 pattern, R10 corners, R2 edge fillets."

### 2. Assumption review (the important part)
Each assumption as a closed, answerable item — the user should be able to respond with a number or a word, not an essay:
```
A1 [confidence: MED] Units — no unit note in title block. Assumed mm
    (values 120/60/25 typical for mm). If inches, say "A1: inches" and I'll rebuild.
A2 [confidence: HIGH] Hole depth — 4× Ø5.5 shown with hidden lines through
    full thickness, no depth dim. Modeled THRU ALL.
A3 [confidence: MED] Center slot position — no locating dimension; slot appears
    symmetric about the vertical centerline. Modeled centered. Alternative: 2 mm
    offset left would also match the view within drawing precision.
A4 [confidence: HIGH] Note "M6x1.0" — modeled as Ø5.0 tap-drill hole; helical
    thread not modeled (standard practice). Say "A4: model thread" to add cosmetic thread.
```
Ordering: lowest confidence first. HIGH-confidence standard-practice items can be collapsed to one line each.

### 3. Unresolved failures (if any)
Features that failed all retries: which step, what was tried, what the model currently has instead, and what single piece of information would unblock it.

### 4. The ask
End with ONE compact prompt:
> "Reply 'approved' to finalize, or correct any item by ID (e.g., 'A1: inches, A3: offset 2mm left'). I'll rebuild automatically from the corrected plan."

## Handling the user's response
- Corrections map to plan-step edits (this is why assumptions carry step ids). Rebuild = rerun the interpreter on the edited `plan.json`; never hand-patch the model.
- After rebuild, rerun full validation and show a diff-style confirmation ("A1 applied: bbox now 3048×1524×635 mm — flagging: that's implausibly large, please confirm inches" — sanity-check user corrections too; users make mistakes).
- If the user answers only some items, treat unanswered ones as accepted.

## Question quality rules (for the rare question that must be asked)
- Closed over open: "THRU or 15 mm deep?" not "what should I do about the hole?"
- Bundled: all questions in one message, never a drip-feed.
- Self-contained: include the evidence and your best guess so the user can answer from the message alone without reopening the drawing.
- Actionable default: every question states what happens if they just say "go with your guess".

## What the user should EXPECT to provide (design the pipeline around only these)
1. The drawing file(s) at the start.
2. Optional environment facts once per machine (SolidWorks version, template path, output directory) — cache these in a config, never re-ask.
3. The single end-of-run review.
Anything beyond this list appearing in a run is a defect in the pipeline to be engineered away.
