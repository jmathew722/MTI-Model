# LLM provider status

**Resolves REFACTOR_ANALYSIS §2.2** ("decide the OpenAI provider's real status").
The complaint was precise: `tests/test_ai_provider.py` with no live traffic
behind it is a *false sense of coverage*, and an unexercised adapter is ongoing
tax. This file is the status, and the code now states it out loud at runtime.

## Status table

| Provider | `AI_PROVIDER` | Default model | Status | Meaning |
|---|---|---|---|---|
| Anthropic | unset (default) | `claude-sonnet-5` | **production** | exercised by real drawing runs; the path every golden artifact came from |
| OpenAI | `openai` | `gpt-5.6` | **live_plumbing_verified** | a REAL call round-trips; drawing-extraction QUALITY still unmeasured |

## Live verification — 2026-08-16

One minimal call was made against the real API through the adapter (a synthetic
prompt, no drawing data), because a test suite with no live traffic behind it
proves nothing about the wire format:

```
model        gpt-5.6
request      forced tool call (tool_choice), reasoning_effort="none"
stop_reason  tool_use                       <- translated from finish_reason
tool call    report_reading {"value": 2.5, "unit": "IN"}
usage        input=157 output=24 cache_read=0 cache_write=0
cost         $0.001505  (priced by usage_log.estimate_cost)
verdict      PASS
```

So the whole translation chain is real-world correct: Anthropic-shaped request →
OpenAI Chat Completions → forced tool call honored → response and usage
translated back into Anthropic's shape → priced by the shared ledger.

**What this still does NOT prove:** extraction QUALITY on real engineering
drawings (vision reading of dimension text, callout parsing, cross-view
reasoning) and real per-part cost. That needs the batch comparison below, which
is the remaining gate to "production".

`pipeline.ai_provider.provider_status()` returns this programmatically, and
`build_client()` logs a warning **once per process** when a
less-than-production path is selected. Selecting an unverified provider is
allowed — it is never silent.

## What "adapter_tested" now actually covers

§2.2's fix was to give the path real coverage or strip it back. It got real
coverage — of the thing the abstraction claims. `tests/test_ai_provider.py`
drives **the real stage entry points** through the OpenAI adapter over a fake
OpenAI SDK client (no key, no network) and asserts a well-formed OpenAI request
comes out:

* **Stage 1.5** `overview_analysis.analyze_overview` — forced tool call, correct
  tool name, `reasoning_effort="none"` on the reasoning-model family.
* **Stage 11** `overview_validate.extract_overview_features` — image really
  crosses as a data URL, `max_completion_tokens` mapped, tool choice forced.
* **Stage 2.6** `must_meet.parse_spec_text_llm` — constraint tool round-trips.
* **Cost accounting** — OpenAI usage translated into Anthropic's convention
  (`input_tokens` = uncached, `cache_read_input_tokens` = cached,
  `cache_creation_input_tokens` = 0) and priced by `usage_log.estimate_cost`, so
  the ledger cannot silently under-report on this path.

That is the honest boundary: **the plumbing is proven — now both in unit tests
and against the live API (see above) — while the OUTPUT QUALITY is not.**
Extraction accuracy on real drawing images and real-world per-part cost remain
unmeasured on the OpenAI path.

## To promote OpenAI to "production" — one command

The remaining gate is drawing-extraction QUALITY, which costs real money on both
keys. That comparison is packaged as a runnable tool rather than a list of
instructions, so the only thing left is the decision to spend:

```powershell
# what it would run and why it costs money — no API calls
python tools\compare_providers.py --parts ..\test_drawings\Test2 --dry-run

# the real comparison (prompts before spending; --yes to skip)
python tools\compare_providers.py --parts ..\test_drawings\Test2 --limit 3
```

It extracts each part once per provider with the **cache disabled** (a cached
Anthropic extraction compared against a fresh OpenAI one would prove nothing),
with the region pass off to keep it to one read per sheet, and reports:

* per-dimension agreement within 0.5%, and every value where the two differ;
* the **only-anthropic / only-openai** columns — a dimension one provider missed
  entirely matters more than one it read differently;
* hole-callout counts, and measured cost per provider from the usage ledger.

Then, **if and only if** the agreement and the missed-dimension columns are
acceptable, flip `PROVIDER_STATUS["openai"]` to `"production"` in
`pipeline/ai_provider.py` and record the date and numbers here.

This was deliberately NOT run on the owner's key during the refactor: a one- or
two-part sample is too small to justify the switch, so spending on it would buy
an inconclusive answer. The decision — and the budget for a meaningful sample —
belongs to whoever actually intends to use the OpenAI path.

## If that comparison is never run

Strip the adapter back. A provider abstraction that no run exercises costs an
extra layer in `_build_client`, a second pricing table, and a translation module
to keep correct on every prompt change — for zero delivered value. The removal is
small and localized: delete `_OpenAIAdapterClient` and its translation helpers,
keep `build_client`/`default_model` as thin Anthropic wrappers, drop the
`gpt-5.6*` rows from `usage_log.PRICING`, and delete the OpenAI half of
`tests/test_ai_provider.py`. No call site changes either way — which was the
adapter's design goal and is also what makes removing it cheap.
