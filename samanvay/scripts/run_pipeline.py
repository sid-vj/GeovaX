#!/usr/bin/env python3
"""Run the full SAMANVAY harmonisation over the real Chennai corpus.

    python scripts/run_pipeline.py --aoi core --out out/chennai
    python scripts/run_pipeline.py --aoi test --out out/tile     # small, for a quick check
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from samanvay.pipeline.harmonise import HarmoniseConfig, HarmonisationPipeline  # noqa: E402
from samanvay.pipeline.presets import AOIS, default_layers as layers  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default="data/aoi")
    ap.add_argument("--out", default="out/chennai")
    ap.add_argument("--aoi", default="core", choices=sorted(AOIS))
    ap.add_argument("--max-features", type=int, default=None)
    ap.add_argument("--no-checkpoints", action="store_true")
    args = ap.parse_args()

    name, bbox = AOIS[args.aoi]
    cfg = HarmoniseConfig(
        aoi_name=name,
        bbox=bbox,
        out_dir=args.out,
        checkpoint_dir=None if args.no_checkpoints else os.path.join(args.out, ".ckpt"),
        parcel_pairs=(("TNGIS_CADASTRE", "NCSCM_CADASTRE"),),
        building_pairs=(("GCC_BUILDINGS", "GOOGLE_OPEN_BUILDINGS"),),
    )
    specs = layers(args.data, args.max_features)
    pipe = HarmonisationPipeline(specs, cfg)

    def progress(r) -> None:
        if r.status in ("ok", "cached", "failed"):
            mark = {"ok": "✓", "cached": "·", "failed": "✗"}[r.status]
            print(f"  {mark} {r.name:<12} {r.seconds:7.1f}s "
                  f"{r.error or ''}", flush=True)

    pipe.dag.on_stage(progress)

    print(f"SAMANVAY — {name} {bbox}", flush=True)
    out = pipe.run()

    print("\n=== stage reports ===")
    for stage in pipe.dag.stages:
        rep = pipe.dag.results[stage].report
        if rep:
            print(f"\n[{stage}]")
            print(_fmt(rep, indent=2))
    print("\n=== outputs ===")
    print(f"  {out.metrics['outputs']}")
    print(f"  ledger: {out.metrics['ledger']['message']}, "
          f"root {out.metrics['ledger']['merkle_root'][:16]}...")
    print(f"  total {out.metrics['total_seconds']}s -> {os.path.abspath(args.out)}")
    return 0


def _fmt(obj, indent: int = 0) -> str:
    pad = " " * indent
    if isinstance(obj, dict):
        lines = []
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{pad}{k}:")
                lines.append(_fmt(v, indent + 2))
            else:
                lines.append(f"{pad}{k}: {v}")
        return "\n".join(lines)
    if isinstance(obj, list):
        return "\n".join(f"{pad}- {_fmt(v, 0) if isinstance(v, (dict, list)) else v}"
                         for v in obj[:8])
    return f"{pad}{obj}"


if __name__ == "__main__":
    raise SystemExit(main())
