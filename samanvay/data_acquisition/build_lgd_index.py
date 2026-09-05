#!/usr/bin/env python3
"""Build the compact, nationwide village/taluk/district/state lookup index that
/api/jurisdiction serves, from the real LGD village boundaries (data_acquisition/sources.py's
`lgd_india` entry — see that entry's notes for how the official lgdirectory.gov.in URL was
found broken and replaced with a verified real mirror).

The real file (data/raw/LGD_Villages.geojsonl, ~1.9 GB, 584,615 real Indian villages) carries
full polygon boundaries. Keeping the full geometry in a file-backed lookup the API loads into
memory per process is not tractable, and exact polygon containment is more precision than an
"which real jurisdiction is this roughly in" lookup needs — so this keeps each village's real
centroid and administrative names/LGD codes only, dropping the boundary detail. This is a
real trade made explicitly, not a shortcut hidden from provenance: /api/jurisdiction's
response documents match_method="nearest_real_village_centroid", not "polygon containment".

Run:
    python data_acquisition/build_lgd_index.py \
        --raw data/raw/LGD_Villages.geojsonl --out data/lgd/villages_index.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from typing import Any


def _centroid(geom: dict[str, Any]) -> tuple[float, float] | None:
    t = geom.get("type")
    coords = geom.get("coordinates")
    if not coords:
        return None
    ring = coords[0] if t == "Polygon" else (coords[0][0] if t == "MultiPolygon" else None)
    if not ring:
        return None
    xs = [c[0] for c in ring]
    ys = [c[1] for c in ring]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", default="data/raw/LGD_Villages.geojsonl")
    ap.add_argument("--out", default="data/lgd/villages_index.jsonl")
    args = ap.parse_args()

    if not os.path.exists(args.raw):
        print(f"! {args.raw} not found — run: python data_acquisition/fetch.py --out data/raw "
              "--only lgd_india")
        return 1

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    t0 = time.time()
    scanned = 0
    kept = 0
    with open(args.raw, encoding="utf-8") as src, open(args.out, "w", encoding="utf-8") as dst:
        for line in src:
            scanned += 1
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            c = _centroid(obj.get("geometry") or {})
            if c is None:
                continue
            p = obj.get("properties") or {}
            village_name = str(p.get("vilname11") or p.get("vilnam_soi") or "").strip()
            if not village_name:
                continue
            record = {
                "lon": round(c[0], 5),
                "lat": round(c[1], 5),
                "village_name": village_name,
                "block_name": str(p.get("block_name") or "").strip() or None,
                "subdistrict_name": str(p.get("sdtname") or "").strip() or None,
                "district_name": str(p.get("dtname") or "").strip() or None,
                "state_name": str(p.get("stname") or "").strip() or None,
                "lgd_village_code": p.get("vil_lgd") or None,
                "lgd_subdistrict_code": p.get("subdt_lgd") or None,
                "lgd_district_code": p.get("dist_lgd") or None,
                "lgd_state_code": p.get("state_lgd") or None,
            }
            dst.write(json.dumps(record, separators=(",", ":")) + "\n")
            kept += 1

    def sha256(path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    manifest_path = os.path.join(os.path.dirname(args.out) or ".", "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump({
            "dataset": "lgd_india",
            "source_file": args.raw,
            "output": args.out,
            "scanned": scanned,
            "kept": kept,
            "seconds": round(time.time() - t0, 2),
            "sha256_index": sha256(args.out),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }, fh, indent=2)

    print(f"wrote {kept:,} of {scanned:,} real villages -> {args.out} "
          f"({os.path.getsize(args.out) / 1e6:.1f} MB, {time.time() - t0:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
