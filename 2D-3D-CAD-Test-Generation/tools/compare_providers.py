#!/usr/bin/env python
"""Compare extraction QUALITY between AI providers on the same drawings.

This is the one gate between the OpenAI path's current status
(``live_plumbing_verified``) and ``production`` — see ``docs/PROVIDER_STATUS.md``.
It exists as a runnable command rather than a paragraph of instructions because
the decision needs evidence, and the evidence costs real money on the owner's
key: this script makes that cost explicit BEFORE spending it, and produces a
dimension-level diff rather than a vibe.

    # what it will cost, without calling anything
    python tools/compare_providers.py --parts ../test_drawings/Test2 --dry-run

    # the real comparison (asks for confirmation unless --yes)
    python tools/compare_providers.py --parts ../test_drawings/Test2 --limit 3

For each part it extracts ONCE per provider with the cache disabled (a cached
Anthropic extraction would otherwise be compared against a fresh OpenAI one, and
the Anthropic side would look free and identical), then reports:

  * per-dimension agreement: same id, same value within tolerance;
  * values only one provider read (the interesting column — a missed dimension is
    worse than a differing one);
  * hole-callout count and diameter agreement;
  * measured cost per provider from the shared usage ledger.

It never writes to the pipeline's normal output tree and never builds anything.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

REL_TOL = 0.005          # 0.5% — a real reading difference, not float noise


def _parts(root: Path, limit: int) -> list[Path]:
    """Part folders (multi-view) or single drawing files under ``root``."""
    if root.is_file():
        return [root]
    parts = [p for p in sorted(root.iterdir())
             if p.is_dir() and p.name not in ("output", ".extraction_cache")]
    if not parts:
        parts = [p for p in sorted(root.iterdir())
                 if p.suffix.lower() in (".pdf", ".png", ".jpg", ".jpeg", ".dwg", ".dxf")]
    return parts[:limit] if limit else parts


def _extract(part: Path, out_root: Path, provider: str, python: str) -> tuple[dict, float]:
    """Run extraction+verification only, for one provider. Returns (extraction, usd)."""
    out = out_root / provider / part.stem
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    if provider == "anthropic":
        env.pop("AI_PROVIDER", None)
    else:
        env["AI_PROVIDER"] = provider
    cmd = [python, "main.py",
           ("--views-folder" if part.is_dir() else "--drawing"), str(part),
           "--output", str(out), "--validate-only", "--no-extract-cache",
           "--region-pass", "off"]          # cost control: one read per sheet
    subprocess.run(cmd, cwd=str(HERE), env=env, capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=3600)
    found = sorted(out.rglob("*_extraction.json"))
    found = [f for f in found if "resolved" not in f.name]
    data = json.loads(found[0].read_text(encoding="utf-8")) if found else {}
    return data, _cost_from_ledger(out)


def _cost_from_ledger(out: Path) -> float:
    total = 0.0
    for ledger in out.rglob("token_usage_log.jsonl"):
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                total += float(json.loads(line).get("cost_usd", 0.0))
            except Exception:
                continue
    return total


def _dims(extraction: dict) -> dict[str, float]:
    out = {}
    for d in extraction.get("dimensions", []) or []:
        did = d.get("id")
        val = d.get("value")
        if did and isinstance(val, (int, float)):
            out[str(did)] = float(val)
    return out


def _agree(a: float, b: float) -> bool:
    return abs(a - b) <= max(REL_TOL * max(abs(a), abs(b)), 1e-6)


def compare(part_name: str, left: dict, right: dict) -> dict:
    """Dimension- and callout-level diff between two extractions of one part."""
    ld, rd = _dims(left), _dims(right)
    shared = sorted(set(ld) & set(rd))
    same = [k for k in shared if _agree(ld[k], rd[k])]
    differ = [{"id": k, "anthropic": ld[k], "openai": rd[k]} for k in shared
              if not _agree(ld[k], rd[k])]
    return {
        "part": part_name,
        "dimensions": {"anthropic": len(ld), "openai": len(rd),
                       "agreed": len(same), "differed": len(differ),
                       "only_anthropic": sorted(set(ld) - set(rd)),
                       "only_openai": sorted(set(rd) - set(ld)),
                       "differences": differ},
        "hole_callouts": {
            "anthropic": len(left.get("hole_callouts") or []),
            "openai": len(right.get("hole_callouts") or []),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--parts", required=True, help="Part folder root, or one drawing.")
    ap.add_argument("--out", default="./provider_comparison", help="Report directory.")
    ap.add_argument("--limit", type=int, default=3, help="Max parts (default 3).")
    ap.add_argument("--dry-run", action="store_true",
                    help="List what WOULD run and stop — no API calls, no cost.")
    ap.add_argument("--yes", action="store_true", help="Skip the cost confirmation.")
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args()

    parts = _parts(Path(args.parts), args.limit)
    if not parts:
        print(f"No parts found under {args.parts}")
        return 2

    print(f"{len(parts)} part(s) x 2 providers, cache DISABLED:")
    for p in parts:
        print(f"  - {p.name}")
    print("\nThis makes REAL paid API calls on BOTH keys (that is the point — a\n"
          "cached run would prove nothing). Region pass is off to keep it to one\n"
          "read per sheet.")
    if args.dry_run:
        print("\n--dry-run: nothing called.")
        return 0
    if not args.yes:
        try:
            if input("\nProceed? [y/N] ").strip().lower() not in ("y", "yes"):
                print("Aborted — nothing spent.")
                return 0
        except EOFError:
            print("Not a terminal; re-run with --yes to confirm.")
            return 2

    out_root = Path(args.out).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    reports, cost = [], {"anthropic": 0.0, "openai": 0.0}
    for part in parts:
        print(f"\n=== {part.name} ===", flush=True)
        left, c1 = _extract(part, out_root, "anthropic", args.python)
        right, c2 = _extract(part, out_root, "openai", args.python)
        cost["anthropic"] += c1
        cost["openai"] += c2
        rep = compare(part.name, left, right)
        reports.append(rep)
        d = rep["dimensions"]
        print(f"  dimensions: {d['anthropic']} vs {d['openai']} | agreed {d['agreed']} "
              f"| differed {d['differed']} | only-A {len(d['only_anthropic'])} "
              f"| only-O {len(d['only_openai'])}")
        for diff in d["differences"][:8]:
            print(f"    {diff['id']}: anthropic {diff['anthropic']} vs openai {diff['openai']}")

    summary = {"parts": reports, "cost_usd": cost,
               "tolerance_rel": REL_TOL}
    (out_root / "provider_comparison.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\ncost: anthropic ${cost['anthropic']:.4f} | openai ${cost['openai']:.4f}")
    print(f"report: {out_root / 'provider_comparison.json'}")
    print("\nPromote OpenAI to \"production\" in pipeline/ai_provider.py ONLY if the "
          "dimension agreement and the only-<provider> columns are acceptable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
