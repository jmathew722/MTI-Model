# LLM provider status

**Resolves REFACTOR_ANALYSIS §2.2** ("decide the OpenAI provider's real status").
The complaint was precise: `tests/test_ai_provider.py` with no live traffic
behind it is a *false sense of coverage*, and an unexercised adapter is ongoing
tax. This file is the status, and the code now states it out loud at runtime.

## Status table

| Provider | `AI_PROVIDER` | Default model | Status | Meaning |
|---|---|---|---|---|
| Anthropic | unset (default) | `claude-sonnet-5` | **production** | exercised by real drawing runs; the path every golden artifact came from |
| OpenAI | `openai` | `gpt-5.6` | **adapter_tested** | the translation layer is unit-tested at EVERY call site; NOT verified end-to-end against production drawings |

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

That is the honest boundary: **the plumbing is proven; the OUTPUT QUALITY is
not.** Extraction accuracy, tool-call reliability under real drawing images, and
real-world cost are unmeasured on the OpenAI path.

## To promote OpenAI to "production"

1. Run the standard test batch on both providers:
   `python main.py --views-folder ..\test_drawings\Test2 --output ..\test_drawings\Test2\output`
   with `AI_PROVIDER` unset, then with `AI_PROVIDER=openai` (and
   `--no-extract-cache`, or the cached Anthropic extraction is what you measure).
2. Diff the per-part `_extraction.json` and READY status; record dimension-level
   disagreements in `Learning Loop/`.
3. Compare `token_usage_log.txt` cost lines for the same parts.
4. If quality holds, flip `PROVIDER_STATUS["openai"]` to `"production"` in
   `pipeline/ai_provider.py` and note the verification date here.

## If that comparison is never run

Strip the adapter back. A provider abstraction that no run exercises costs an
extra layer in `_build_client`, a second pricing table, and a translation module
to keep correct on every prompt change — for zero delivered value. The removal is
small and localized: delete `_OpenAIAdapterClient` and its translation helpers,
keep `build_client`/`default_model` as thin Anthropic wrappers, drop the
`gpt-5.6*` rows from `usage_log.PRICING`, and delete the OpenAI half of
`tests/test_ai_provider.py`. No call site changes either way — which was the
adapter's design goal and is also what makes removing it cheap.
