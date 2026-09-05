#!/usr/bin/env python3
"""Publish the real GCC ward/zone/CMA administrative boundaries as served collections.

These are real, already AOI-clipped government boundaries (data_acquisition/build_aoi.py
already produces them — see gcc_wards/gcc_zones/cma_boundary in data_acquisition/sources.py)
that were, until now, only catalogued and disk-checked by /api/provenance, never actually
served. Like utilities.geojson (see build_utilities_layer.py), these are not run through the
multi-source harmonisation pipeline — there is exactly one real boundary source per admin
tier, and matching/confidence fusion need at least two independent sources to mean anything.
They're served as direct reference layers instead.

Run:
    python scripts/build_admin_layers.py --aoi-dir data/aoi_metro --out out/chennai_metro
"""
from __future__ import annotations

import argparse
import json
import os
import shutil


LAYERS = [
    ("wards_gcc.geojson", "wards.geojson"),
    ("zones_gcc.geojson", "zones.geojson"),
    ("cma_gcc.geojson", "cma.geojson"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--aoi-dir", default="data/aoi_metro",
                     help="Directory already holding the AOI-clipped wards/zones/cma files "
                          "(produced by data_acquisition/build_aoi.py)")
    ap.add_argument("--out", default="out/chennai_metro")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    wrote_any = False
    for src_name, dest_name in LAYERS:
        src_path = os.path.join(args.aoi_dir, src_name)
        if not os.path.exists(src_path):
            print(f"  ! missing {src_path}, skipping")
            continue
        with open(src_path, encoding="utf-8") as fh:
            data = json.load(fh)
        n = len(data.get("features", []))
        dest_path = os.path.join(args.out, dest_name)
        shutil.copyfile(src_path, dest_path)
        print(f"  published {n} real features -> {dest_path}")
        wrote_any = True

    return 0 if wrote_any else 1


if __name__ == "__main__":
    raise SystemExit(main())
