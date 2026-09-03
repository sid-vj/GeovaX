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

from samanvay.core.models import FeatureClass, SourceType  # noqa: E402
from samanvay.pipeline.harmonise import (HarmoniseConfig, HarmonisationPipeline,  # noqa: E402
                                         LayerSpec)

AOIS = {
    "core": ("Chennai Central", (80.20, 13.03, 80.28, 13.11)),
    "test": ("Chennai Chetpet tile", (80.235, 13.070, 80.250, 13.085)),
    "mid":  ("Chennai Nungambakkam-Kilpauk", (80.225, 13.045, 80.265, 13.095)),
}


def layers(data_dir: str, max_features: int | None) -> list[LayerSpec]:
    return [
        LayerSpec(
            dataset_id="TNGIS_CADASTRE",
            path=os.path.join(data_dir, "cadastre_tngis.geojsonl"),
            source_type=SourceType.CADASTRAL_MAP,
            feature_class=FeatureClass.PARCEL,
            authority="TNGIS", licence="CC0-1.0", accuracy_m=3.0, vintage="2023",
            id_fields=("survey_number", "lgd_village_code"),
            role="reference", max_features=max_features,
        ),
        LayerSpec(
            dataset_id="NCSCM_CADASTRE",
            path=os.path.join(data_dir, "cadastre_ncscm.geojsonl"),
            source_type=SourceType.CADASTRAL_MAP,
            feature_class=FeatureClass.PARCEL,
            authority="NCSCM", licence="CC0-1.0", accuracy_m=5.0, vintage="2019",
            id_fields=("Survey_Number", "Village"),
            role="candidate", max_features=max_features,
        ),
        LayerSpec(
            dataset_id="GCC_BUILDINGS",
            path=os.path.join(data_dir, "buildings_gcc.geojsonl"),
            source_type=SourceType.MUNICIPAL_GIS,
            feature_class=FeatureClass.BUILDING,
            authority="GCC", licence="CC0-1.0", accuracy_m=1.0, vintage="2024",
            id_fields=("gcc_gis_id",),
            role="reference", max_features=max_features,
        ),
        LayerSpec(
            dataset_id="GOOGLE_OPEN_BUILDINGS",
            path=os.path.join(data_dir, "buildings_gob.geojsonl"),
            source_type=SourceType.AI_EXTRACTION,
            feature_class=FeatureClass.BUILDING,
            authority="NRSC", licence="CC-BY-4.0", accuracy_m=1.8, vintage="2023",
            id_fields=("gob_id",),
            role="candidate", max_features=max_features,
        ),
    ]


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
